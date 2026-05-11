from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG      = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Terceros
    'django_celery_results',
    # Proyecto
    'backups',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # sirve estáticos en producción
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]

DATABASES = {
    'default': {
        'ENGINE'  : config('DB_ENGINE', default='django.db.backends.sqlite3'),
        'NAME'    : config('DB_NAME',   default=str(BASE_DIR / 'db.sqlite3')),
        'USER'    : config('DB_USER',   default=''),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST'    : config('DB_HOST',   default=''),
        'PORT'    : config('DB_PORT',   default=''),
        'OPTIONS' : {
            'connect_timeout': 10,
        },
        'CONN_MAX_AGE': 60,   # reutiliza conexiones por 60s (mejor rendimiento)
    }
}

LANGUAGE_CODE = 'es-pe'
TIME_ZONE     = 'America/Lima'
USE_I18N      = True
USE_TZ        = True

STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Celery ───────────────────────────────────────
CELERY_BROKER_URL         = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND     = 'django-db'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_SERIALIZER    = 'json'
CELERY_ACCEPT_CONTENT     = ['json']

# ── SGBM ─────────────────────────────────────────
SGBM = {
    'NODO_A_HOST'  : config('NODO_A_HOST'),
    'NODO_A_USER'  : config('NODO_A_USER'),
    'NODO_A_KEY'   : config('NODO_A_KEY'),
    'NODO_A_MOODLE': config('NODO_A_MOODLE', default='/var/www/html'),
    'NODO_A_MOOSH' : config('NODO_A_MOOSH',  default='php /opt/moosh/moosh.php -n'),
    'NODO_A_TEMP'  : config('NODO_A_TEMP',   default='/tmp/moodle_bkp'),
    'NODO_B_BACKUP': config('NODO_B_BACKUP', default='/backups'),
    'DELAY_CURSOS' : 3,
}