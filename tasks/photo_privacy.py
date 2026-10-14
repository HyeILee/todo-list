"""Remove identifying metadata from new uploads without modifying stored originals."""
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


class PhotoPrivacyError(ValueError):
    pass


def strip_photo_metadata(content, content_type, max_bytes):
    formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                image_format = formats.get(content_type)
                if source.format != image_format or not image_format:
                    raise PhotoPrivacyError("사진 형식이 올바르지 않습니다.")
                frames_count = getattr(source, "n_frames", 1)
                # Bound decoding memory and work, including animation frames.
                if source.width * source.height > 25_000_000 or frames_count > 200 or source.width * source.height * frames_count > 50_000_000:
                    raise PhotoPrivacyError("사진의 해상도나 프레임 수가 너무 큽니다.")
                frames, durations = [], []
                icc_profile = source.info.get("icc_profile")
                loop = source.info.get("loop", 0)
                default_image = image_format == "PNG" and source.info.get("default_image", False)
                for index in range(frames_count):
                    source.seek(index)
                    oriented = ImageOps.exif_transpose(source)
                    frame = oriented.convert("RGB" if image_format == "JPEG" else "RGBA")
                    # Construct a fresh image: convert/copy alone can retain source info.
                    clean = Image.new(frame.mode, frame.size)
                    clean.paste(frame)
                    frames.append(clean)
                    durations.append(source.info.get("duration", 100))
                options = {}
                if icc_profile:
                    options["icc_profile"] = icc_profile
                if image_format == "JPEG":
                    options.update(quality=95, subsampling=0, optimize=True)
                elif image_format == "WEBP":
                    options.update(lossless=True)
                if frames_count > 1:
                    options.update(save_all=True, append_images=frames[1:], duration=durations[1:] if default_image else durations, loop=loop)
                    if default_image:
                        options["default_image"] = True
                output = BytesIO()
                frames[0].save(output, format=image_format, **options)
                sanitized = output.getvalue()
                # Re-encoding a lossy phone photo as high-quality JPEG or
                # lossless WebP can grow it beyond the upload limit. Keep the
                # clean frames and retry bounded compression only when needed.
                if len(sanitized) > max_bytes and image_format in {"JPEG", "WEBP"}:
                    for quality in (90, 82, 74, 66, 58):
                        compressed_options = {**options, "quality": quality}
                        if image_format == "JPEG":
                            compressed_options["subsampling"] = 2
                        else:
                            compressed_options["lossless"] = False
                        output = BytesIO()
                        frames[0].save(output, format=image_format, **compressed_options)
                        sanitized = output.getvalue()
                        if len(sanitized) <= max_bytes:
                            break
    except PhotoPrivacyError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise PhotoPrivacyError("사진을 읽을 수 없습니다. 정상적인 JPEG, PNG, WebP 파일을 선택해 주세요.") from error
    if len(sanitized) > max_bytes:
        raise PhotoPrivacyError("사진 정보 제거 후 용량이 2.5MB를 넘습니다. 사진 크기를 줄여 다시 올려 주세요.")
    return sanitized
