from backend.runtime import adapter_file_policy
from backend.runtime import adapter_app, adapter_workspace


def test_runtime_adapter_file_policy_is_shared_by_adapter_and_host_helper():
    assert adapter_file_policy.MAX_INLINE_FILE_WRITE_BYTES == 10 * 1024 * 1024
    assert adapter_file_policy.MAX_FILE_DOWNLOAD_BYTES == 50 * 1024 * 1024
    assert adapter_app.MAX_FILE_WRITE_BYTES is adapter_file_policy.MAX_INLINE_FILE_WRITE_BYTES
    assert adapter_app.MAX_FILE_DOWNLOAD_BYTES is adapter_file_policy.MAX_FILE_DOWNLOAD_BYTES
    assert adapter_workspace.MAX_UPLOAD_FILE_BYTES is adapter_file_policy.MAX_INLINE_FILE_WRITE_BYTES
