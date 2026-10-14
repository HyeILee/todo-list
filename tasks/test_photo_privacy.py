from io import BytesIO
import random
from unittest import mock

from PIL import Image, PngImagePlugin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import DailyPhoto
from .photo_privacy import PhotoPrivacyError, strip_photo_metadata


class PhotoPrivacyTests(SimpleTestCase):
    def encoded(self, image, image_format, **options):
        stream = BytesIO()
        image.save(stream, format=image_format, **options)
        return stream.getvalue()

    def test_jpeg_orientation_and_identifying_exif_are_removed(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "private author"
        exif[34853] = {1: "N", 2: (37.0, 12.0, 1.0), 3: "E", 4: (127.0, 1.0, 2.0)}
        content = self.encoded(Image.new("RGB", (3, 2), "red"), "JPEG", exif=exif)
        clean = strip_photo_metadata(content, "image/jpeg", 2_500_000)
        with Image.open(BytesIO(clean)) as result:
            self.assertEqual(result.size, (2, 3))
            self.assertEqual(dict(result.getexif()), {})
        self.assertNotIn(b"private author", clean)

    def test_png_text_exif_and_alpha(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("location", "private coordinates")
        exif = Image.Exif()
        exif[315] = "private author"
        content = self.encoded(Image.new("RGBA", (2, 2), (20, 40, 60, 64)), "PNG", pnginfo=metadata, exif=exif)
        clean = strip_photo_metadata(content, "image/png", 2_500_000)
        with Image.open(BytesIO(clean)) as result:
            self.assertEqual(result.getpixel((0, 0)), (20, 40, 60, 64))
            self.assertEqual(dict(result.getexif()), {})
            self.assertNotIn("location", result.info)

    def test_animation_frames_duration_and_loop_survive_for_png_and_webp(self):
        first = Image.new("RGBA", (3, 2), "red")
        second = Image.new("RGBA", (3, 2), "blue")
        for image_format, content_type in (("PNG", "image/png"), ("WEBP", "image/webp")):
            with self.subTest(format=image_format):
                exif = Image.Exif()
                exif[315] = "private author"
                content = self.encoded(first, image_format, save_all=True, append_images=[second], duration=[100, 200], loop=2, exif=exif, lossless=True)
                clean = strip_photo_metadata(content, content_type, 2_500_000)
                with Image.open(BytesIO(clean)) as result:
                    self.assertEqual(result.n_frames, 2)
                    self.assertEqual(result.info["loop"], 2)
                    self.assertEqual(dict(result.getexif()), {})
                    result.seek(1)
                    result.load()
                    self.assertEqual(result.convert("RGBA").getpixel((0, 0)), (0, 0, 255, 255))
                    self.assertEqual(result.info["duration"], 200)
                self.assertNotIn(b"private author", clean)

    def test_invalid_and_oversize_result_are_rejected(self):
        with self.assertRaises(PhotoPrivacyError):
            strip_photo_metadata(b"\xff\xd8\xffnot-a-photo", "image/jpeg", 2_500_000)
        with self.assertRaises(PhotoPrivacyError):
            strip_photo_metadata(self.encoded(Image.new("RGB", (2, 2)), "JPEG"), "image/jpeg", 10)

    def test_compressed_phone_photos_stay_within_limit_after_metadata_removal(self):
        image = Image.frombytes("RGB", (1700, 1300), random.Random(184).randbytes(1700 * 1300 * 3))
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "private phone author"
        for image_format, content_type in (("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(format=image_format):
                content = self.encoded(image, image_format, quality=75, exif=exif)
                self.assertLess(len(content), 2_500_000)
                # Ensure this exercises the formerly rejected expanded output.
                unrestricted = strip_photo_metadata(content, content_type, 20_000_000)
                self.assertGreater(len(unrestricted), 2_500_000)
                clean = strip_photo_metadata(content, content_type, 2_500_000)
                self.assertLessEqual(len(clean), 2_500_000)
                with Image.open(BytesIO(clean)) as result:
                    result.load()
                    self.assertEqual(result.size, (1300, 1700))
                    self.assertEqual(result.format, image_format)
                    self.assertEqual(dict(result.getexif()), {})
                self.assertNotIn(b"private phone author", clean)


class PhotoPrivacyUploadTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="privacy-owner", password="Long-fixture-pass-841"))

    @mock.patch("tasks.views.photo_storage_configured", return_value=True)
    @mock.patch("tasks.views.upload_private_photo")
    def test_corrupt_image_never_reaches_storage(self, upload, configured):
        photo = SimpleUploadedFile("broken.jpg", b"\xff\xd8\xffnot-a-photo", content_type="image/jpeg")
        response = self.client.post(reverse("photo_upload", args=["2026-10-02"]), {"photo": photo, "category": "LIFE"})
        self.assertEqual(response.status_code, 400)
        upload.assert_not_called()
        self.assertFalse(DailyPhoto.objects.exists())

    @mock.patch("tasks.views.photo_storage_configured", return_value=True)
    @mock.patch("tasks.views.upload_private_photo", return_value=("https://fixture.private.blob.vercel-storage.com/photo.webp", "photos/fixture.webp"))
    def test_compressed_webp_upload_is_sanitized_and_registered(self, upload, configured):
        image = Image.frombytes("RGB", (1600, 1200), random.Random(184).randbytes(1600 * 1200 * 3))
        exif = Image.Exif()
        exif[315] = "private phone author"
        stream = BytesIO()
        image.save(stream, format="WEBP", quality=75, exif=exif)
        photo = SimpleUploadedFile("phone.webp", stream.getvalue(), content_type="image/webp")
        response = self.client.post(reverse("photo_upload", args=["2026-10-06"]), {"photo": photo, "category": "LIFE"})
        self.assertEqual(response.status_code, 201)
        saved = upload.call_args.args[1]
        self.assertLessEqual(len(saved), 2_500_000)
        with Image.open(BytesIO(saved)) as result:
            result.load()
            self.assertEqual(dict(result.getexif()), {})
        self.assertEqual(DailyPhoto.objects.get().size_bytes, len(saved))
