from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.chat_page, name="page"),
    path("api/chat/", views.chat_api, name="chat-api"),
    path("api/model/", views.set_model_api, name="set-model-api"),
    path("api/dashboard/", views.dashboard_api, name="dashboard-api"),
    path("api/history/clear/", views.clear_history_api, name="clear-history-api"),
]
