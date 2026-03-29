try:
    from .main import main
except ImportError:
    from mkw_tracker.main import main

main()
