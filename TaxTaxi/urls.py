from django.urls import path
from . import views

app_name = 'taxtaxi'

urlpatterns = [
    path('', views.shipment_calculator, name='calculator'),
]
