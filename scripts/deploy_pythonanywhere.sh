#!/bin/bash
set -euo pipefail

# One-shot deploy script for Django on PythonAnywhere.
# Usage example:
#   bash scripts/deploy_pythonanywhere.sh \
#     --repo-url https://github.com/<you>/<repo>.git \
#     --project-name mid_exam_prj \
#     --venv-name fengcloud-venv \
#     --python-bin python3.11 \
#     --gemini-api-key "<KEY>" \
#     --gemini-model gemini-2.5-flash-lite \
#     --secret-key "<DJANGO_SECRET_KEY>"
#
# Optional:
#   --skip-git-pull
#   --wsgi-file /var/www/<username>_pythonanywhere_com_wsgi.py
#   --superuser-username admin --superuser-email you@example.com --superuser-password "..."

REPO_URL=""
PROJECT_NAME="mid_exam_prj"
VENV_NAME="fengcloud-venv"
PYTHON_BIN="python3.11"
GEMINI_API_KEY=""
GEMINI_MODEL="gemini-2.5-flash-lite"
DJANGO_SECRET_KEY=""
DJANGO_DEBUG="False"
WSGI_FILE=""
SKIP_GIT_PULL="0"
SUPERUSER_USERNAME=""
SUPERUSER_EMAIL=""
SUPERUSER_PASSWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="$2"; shift 2 ;;
    --project-name)
      PROJECT_NAME="$2"; shift 2 ;;
    --venv-name)
      VENV_NAME="$2"; shift 2 ;;
    --python-bin)
      PYTHON_BIN="$2"; shift 2 ;;
    --gemini-api-key)
      GEMINI_API_KEY="$2"; shift 2 ;;
    --gemini-model)
      GEMINI_MODEL="$2"; shift 2 ;;
    --secret-key)
      DJANGO_SECRET_KEY="$2"; shift 2 ;;
    --django-debug)
      DJANGO_DEBUG="$2"; shift 2 ;;
    --wsgi-file)
      WSGI_FILE="$2"; shift 2 ;;
    --skip-git-pull)
      SKIP_GIT_PULL="1"; shift ;;
    --superuser-username)
      SUPERUSER_USERNAME="$2"; shift 2 ;;
    --superuser-email)
      SUPERUSER_EMAIL="$2"; shift 2 ;;
    --superuser-password)
      SUPERUSER_PASSWORD="$2"; shift 2 ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

PA_USER="$(whoami)"
PROJECT_DIR="$HOME/$PROJECT_NAME"
DEFAULT_WSGI_FILE="/var/www/${PA_USER}_pythonanywhere_com_wsgi.py"

log() { echo "[deploy] $*"; }
warn() { echo "[warn] $*" >&2; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[ERROR] Missing command: $1" >&2
    exit 1
  fi
}

auto_detect_wsgi_file() {
  local default_path="$1"
  local user="$2"
  local candidates=()
  local preferred="$default_path"

  if [[ -f "$preferred" ]]; then
    echo "$preferred"
    return 0
  fi

  if ls /var/www/*_wsgi.py >/dev/null 2>&1; then
    mapfile -t candidates < <(ls -1 /var/www/*_wsgi.py 2>/dev/null)
  fi

  if [[ ${#candidates[@]} -eq 1 ]]; then
    echo "${candidates[0]}"
    return 0
  fi

  if [[ ${#candidates[@]} -gt 1 ]]; then
    for path in "${candidates[@]}"; do
      if [[ "$path" == *"${user}_pythonanywhere_com_wsgi.py" ]]; then
        echo "$path"
        return 0
      fi
    done
    # keep empty so caller can force user to choose --wsgi-file
    echo ""
    return 0
  fi

  echo ""
}

upsert_env() {
  local key="$1"
  local value="$2"
  local env_file="$3"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[\/&]/\\&/g')"
  if grep -qE "^${key}=" "$env_file"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$env_file"
  else
    printf "%s=%s\n" "$key" "$value" >> "$env_file"
  fi
}

log "Checking required commands..."
need_cmd git
need_cmd python3
need_cmd pip

if [[ -z "$WSGI_FILE" ]]; then
  WSGI_FILE="$(auto_detect_wsgi_file "$DEFAULT_WSGI_FILE" "$PA_USER")"
  if [[ -n "$WSGI_FILE" ]]; then
    log "Detected WSGI file: $WSGI_FILE"
  else
    warn "Could not auto-detect WSGI file."
    warn "Create your web app first on PythonAnywhere Web tab."
    warn "Or rerun with --wsgi-file /var/www/<your_wsgi_filename>.py"
  fi
fi

log "Loading virtualenvwrapper..."
if ! command -v mkvirtualenv >/dev/null 2>&1; then
  # PythonAnywhere typically supports this shorthand:
  # shellcheck source=/dev/null
  source virtualenvwrapper.sh || true
fi
if ! command -v mkvirtualenv >/dev/null 2>&1; then
  echo "[ERROR] mkvirtualenv not found. Open a PythonAnywhere Bash console and run: source virtualenvwrapper.sh" >&2
  exit 1
fi

log "Preparing project directory: $PROJECT_DIR"
if [[ -d "$PROJECT_DIR/.git" ]]; then
  if [[ "$SKIP_GIT_PULL" != "1" ]]; then
    log "Project exists, pulling latest code..."
    git -C "$PROJECT_DIR" pull --ff-only
  else
    log "Skipping git pull (--skip-git-pull)."
  fi
elif [[ -d "$PROJECT_DIR" ]]; then
  warn "$PROJECT_DIR exists but is not a git repo. Keeping existing files."
elif [[ -n "$REPO_URL" ]]; then
  log "Cloning repository..."
  git clone "$REPO_URL" "$PROJECT_DIR"
else
  echo "[ERROR] Project folder not found and --repo-url not provided." >&2
  exit 1
fi

cd "$PROJECT_DIR"

log "Creating virtualenv if needed: $VENV_NAME"
if ! lsvirtualenv -b | grep -Fxq "$VENV_NAME"; then
  mkvirtualenv "$VENV_NAME" --python="$PYTHON_BIN"
fi

log "Activating virtualenv: $VENV_NAME"
workon "$VENV_NAME"

log "Installing dependencies..."
python -m pip install --upgrade pip wheel setuptools
if [[ -f requirements.txt ]]; then
  pip install -r requirements.txt
else
  echo "[ERROR] requirements.txt not found in $PROJECT_DIR" >&2
  exit 1
fi

if [[ -z "$DJANGO_SECRET_KEY" ]]; then
  log "No --secret-key provided, generating one."
  DJANGO_SECRET_KEY="$(python - <<'PY'
from secrets import token_urlsafe
print(token_urlsafe(64))
PY
)"
fi

log "Writing .env"
ENV_FILE="$PROJECT_DIR/.env"
touch "$ENV_FILE"
upsert_env "DJANGO_DEBUG" "$DJANGO_DEBUG" "$ENV_FILE"
upsert_env "DJANGO_SECRET_KEY" "$DJANGO_SECRET_KEY" "$ENV_FILE"
upsert_env "GEMINI_MODEL" "$GEMINI_MODEL" "$ENV_FILE"
if [[ -n "$GEMINI_API_KEY" ]]; then
  upsert_env "GEMINI_API_KEY" "$GEMINI_API_KEY" "$ENV_FILE"
else
  warn "No --gemini-api-key provided; chatbot will use local fallback mode."
fi

log "Running Django migrations..."
python manage.py migrate

log "Collecting static files..."
python manage.py collectstatic --noinput

log "Running system checks..."
python manage.py check

if [[ -n "$SUPERUSER_USERNAME" && -n "$SUPERUSER_EMAIL" && -n "$SUPERUSER_PASSWORD" ]]; then
  log "Creating superuser (if not exists)..."
  export DJANGO_SUPERUSER_USERNAME="$SUPERUSER_USERNAME"
  export DJANGO_SUPERUSER_EMAIL="$SUPERUSER_EMAIL"
  export DJANGO_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD"
  python manage.py createsuperuser --noinput || true
else
  warn "Superuser not created. Provide --superuser-username/email/password if needed."
fi

log "Updating WSGI file: $WSGI_FILE"
if [[ -n "$WSGI_FILE" && -f "$WSGI_FILE" ]]; then
  cp "$WSGI_FILE" "${WSGI_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  cat > "$WSGI_FILE" <<EOF
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

project_home = '$PROJECT_DIR'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

load_dotenv(Path(project_home) / '.env')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fengcloud.settings')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
EOF
else
  warn "WSGI file not found or not specified."
  warn "Set the correct file with --wsgi-file /var/www/<your_wsgi_file>.py and rerun."
fi

cat <<EOF

[done] Deployment commands completed.

Next steps on PythonAnywhere Web tab (manual):
1) Virtualenv path:
   /home/$PA_USER/.virtualenvs/$VENV_NAME
2) Source code / Working directory:
   $PROJECT_DIR
3) Static files mapping:
   URL /static/ -> $PROJECT_DIR/staticfiles
4) Media files mapping:
   URL /media/  -> $PROJECT_DIR/media
5) Click "Reload" web app.

EOF
