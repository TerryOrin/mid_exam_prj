from django.urls import path
from . import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("about/", views.about_view, name="about"),
    path("ar-guide/", views.ar_guide_view, name="ar_guide"),
    path("iot-war-room/", views.iot_war_room_view, name="iot_war_room"),
    path("events/", views.events_list_view, name="events_list"),
    path("events/<slug:slug>/", views.event_detail_view, name="event_detail"),
    path("stories/", views.stories_view, name="stories"),
    path("stories/<slug:slug>/", views.story_detail_view, name="story_detail"),
    path("usr/", views.usr_view, name="usr"),
    path("contact/", views.contact_view, name="contact"),
    path("api/ai-guide-chat/", views.ai_guide_chat, name="ai_guide_chat"),
    path("api/chat/", views.chatbot_api, name="chatbot_api"),
    path("api/iot_data/", views.iot_data_api, name="iot_data_api"),
    path("api/ai_diagnose/", views.ai_diagnose_api, name="ai_diagnose_api"),
    path("api/ar-voice/", views.ar_ai_guide_api, name="ar_ai_guide_api"),
]
