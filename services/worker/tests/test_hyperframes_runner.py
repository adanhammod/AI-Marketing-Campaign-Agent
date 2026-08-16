import sys
import textwrap

import pytest

from campaign_worker.errors import WorkflowOperationError
from campaign_worker.video.hyperframes_runner import run_hyperframes_cli


def _fake_binary(tmp_path, script: str) -> str:
    path = tmp_path / "fake_hyperframes.py"
    path.write_text(textwrap.dedent(script))
    return str(path)


@pytest.mark.asyncio
async def test_run_hyperframes_cli_missing_binary_raises_video_provider_unavailable():
    with pytest.raises(WorkflowOperationError) as error:
        await run_hyperframes_cli("/nonexistent/npx-xyz", ["hyperframes", "render"], timeout_seconds=5)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_run_hyperframes_cli_succeeds_on_zero_exit(tmp_path):
    script = _fake_binary(tmp_path, "import sys; sys.exit(0)")
    await run_hyperframes_cli(sys.executable, [script], timeout_seconds=5)


@pytest.mark.asyncio
async def test_run_hyperframes_cli_non_zero_exit_raises_video_provider_unavailable_with_sanitized_stderr(tmp_path):
    script = _fake_binary(
        tmp_path,
        """
        import sys
        sys.stderr.write("x" * 2000)
        sys.exit(1)
        """,
    )
    with pytest.raises(WorkflowOperationError) as error:
        await run_hyperframes_cli(sys.executable, [script], timeout_seconds=5)
    assert error.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True
    assert len(str(error.value)) < 1000


@pytest.mark.asyncio
async def test_run_hyperframes_cli_timeout_kills_the_process_and_raises_provider_timeout(tmp_path):
    script = _fake_binary(tmp_path, "import time; time.sleep(30)")
    with pytest.raises(WorkflowOperationError) as error:
        await run_hyperframes_cli(sys.executable, [script], timeout_seconds=0.2)
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert error.value.retryable is True
