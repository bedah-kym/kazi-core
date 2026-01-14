"""Backend URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path,include
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.authtoken.views import obtain_auth_token
from django.conf import settings
from django.conf.urls.static import static

from chatbot.views import upload_file
from users.views import landing_page

urlpatterns = [
    path('', landing_page, name='landing'),  # Enterprise landing page
    path('admin/', admin.site.urls),
    path('chatbot/',include('chatbot.urls')),
    path('accounts/',include('users.urls')),
    path('accounts/', include('allauth.urls')),  # Social Auth URLs
    path('api/',include('Api.urls')),
    path('travel/', include('travel.urls')),  # Travel Planning UI
    path('auth/', obtain_auth_token),
    path('api-auth/', include('rest_framework.urls')),
    path('uploads/', upload_file, name='upload_file'),
]
urlpatterns = format_suffix_patterns(urlpatterns)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)