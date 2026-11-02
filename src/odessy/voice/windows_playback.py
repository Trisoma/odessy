"""Play one WAV on the default Windows output in an interruptible child process."""
import sys


def main():
    if sys.platform != 'win32' or len(sys.argv) != 2:
        return 2
    import winsound
    try:
        winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    except (OSError, RuntimeError):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
