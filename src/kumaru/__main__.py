"""``python -m kumaru``: the ``kumaru`` command, wherever Python can run.

The desktop shortcuts run ``pythonw -m kumaru app``: pythonw opens no console
window (see ``desktop.py``).
"""

from kumaru.cli import main

if __name__ == "__main__":
    main()
