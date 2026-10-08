"""Start the desktop app (double-click on Windows, or: python app.py)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # needed for the parallel import in the .exe
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        # Used by the build: import a ZIP into a database, exit 0 if employees were found.
        from pdf_research.index import Index

        idx = Index(sys.argv[3])
        idx.build(sys.argv[2])
        sys.exit(0 if idx.stats()["employees"] > 0 else 1)

    from pdf_research.app import main

    main()
