"""``python -m ghuumi``: the ``ghuumi`` command, wherever Python can run.

The desktop shortcuts run ``pythonw -m ghuumi app``: pythonw opens no console
window (see ``desktop.py``).
"""

from ghuumi.cli import main

if __name__ == "__main__":
    main()
