"""Check the running CI container without contacting an external service."""

import time
from urllib.error import URLError
from urllib.request import urlopen


def main():
    for _ in range(40):
        try:
            with urlopen("http://127.0.0.1:7860/_stcore/health", timeout=2) as response:
                if response.status == 200 and response.read().strip() == b"ok":
                    print("Container health endpoint passed.")
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    raise SystemExit("Container startup check failed.")


if __name__ == "__main__":
    main()
