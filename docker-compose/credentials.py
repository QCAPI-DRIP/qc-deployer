DATABASES = {
    'default': {
        'ATOMIC_REQUESTS': True,
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': "awx",
        'USER': "awx",
        'PASSWORD': "awxpass",
        'HOST': "postgres",
        'PORT': "5432",
    }
}

BROKER_URL = 'redis://redis:6379'

BROADCAST_WEBSOCKET_SECRET = "R3M1V2I6TUY4MVptRmxQOnNaTy13SHdmNyxwQk5NZExvOUJQRkFDN181RmFDenZTRVFJci1oZDlBVzVCaUVkbS4wQ2I4QUdGQWk6S1hMdEgtZGx4U25DaTBvLmsxekNZUURROGN3MGtZOjFVa1NsVXAxTUJyRFI5T3JvZXYuTCw="
