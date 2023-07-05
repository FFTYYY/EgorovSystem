from django.urls import path
from .views import get_variable , get_fucntion, add_password, delete_password
from functools import partial

urlpatterns = [

    path("get/variable/<str:key>"       , get_variable) , 
    path("get/fucntion/<str:key>"       , get_fucntion) , 
    path("add_password/<str:key>"       , add_password) , 
    path("delete_password/<str:key>"    , delete_password) , 
]