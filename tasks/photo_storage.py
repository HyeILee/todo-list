import os


class PhotoStorageError(Exception):
    pass


def configured():
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN"))


def upload_private_photo(pathname, content, content_type):
    if not configured():
        raise PhotoStorageError("사진 저장소가 연결되지 않았습니다.")
    try:
        from vercel.blob import put

        result = put(
            pathname,
            content,
            access="private",
            content_type=content_type,
            add_random_suffix=True,
            cache_control_max_age=3600,
        )
    except Exception as error:
        raise PhotoStorageError("사진을 저장소에 올리지 못했습니다.") from error
    return result.url, result.pathname


def download_private_photo(blob_url):
    if not configured():
        raise PhotoStorageError("사진 저장소가 연결되지 않았습니다.")
    try:
        from vercel.blob import get

        result = get(blob_url, access="private", use_cache=True, timeout=20)
    except Exception as error:
        raise PhotoStorageError("사진을 불러오지 못했습니다.") from error
    return result.content


def delete_private_photo(blob_url):
    if not configured():
        raise PhotoStorageError("사진 저장소가 연결되지 않았습니다. 잠시 후 다시 시도해 주세요.")
    try:
        from vercel.blob import delete

        delete(blob_url, token=os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN"))
    except Exception as error:
        raise PhotoStorageError("사진을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.") from error
