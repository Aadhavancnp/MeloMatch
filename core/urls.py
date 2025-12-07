from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about_us, name="about"),
    path("contact/", views.contact_us, name="contact"),
    path("faq/", views.faq_list, name="faq"),
    path("dashboard/", views.dashboard, name="dashboard")
]
