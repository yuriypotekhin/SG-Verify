# SG-Verify server settings on top of the CVAT production settings.
# Mounted into the stock cvat/server image as cvat/settings/sg_verify.py (see docker-compose.yml).
import os

from cvat.settings.production import *  # noqa: F401,F403

# Role of every newly registered user. The review workflow sets verdicts, assignees and guides of any task,
# which needs "admin"; use "user" (CVAT default) if reviewers must not see each other's tasks.
IAM_DEFAULT_ROLE = os.getenv("SG_VERIFY_DEFAULT_ROLE", "admin")

# reviewers sign in with short names (CVAT default: 5 characters)
ACCOUNT_USERNAME_MIN_LENGTH = 3
