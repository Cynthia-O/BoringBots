from dupebot.cli import main

# The guard matters: clustering starts worker processes, and on macOS each worker
# re-imports this module.
if __name__ == "__main__":
    raise SystemExit(main())
