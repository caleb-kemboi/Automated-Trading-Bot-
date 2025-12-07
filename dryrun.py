# dry_run.py
import os
os.environ["DRY_RUN"] = "1"  # Force dry_run
from config import EXCHANGES

# Override dry_run for all exchanges when running dryrun.py
for cfg in EXCHANGES:
    cfg.dry_run = True

from main import main
if __name__ == "__main__":
    print("DRY RUN MODE — NO REAL ORDERS WILL BE PLACED")
    main()