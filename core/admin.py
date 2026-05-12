from django import forms
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import Event, HeroSlide, StoryPost


admin.site.site_title = "風雲客棧後台"
admin.site.site_header = "風雲客棧管理系統"
admin.site.index_title = "內容管理中心"


@admin.register(HeroSlide)
class HeroSlideAdmin(admin.ModelAdmin):
    list_display = ["order", "title", "subtitle_short", "preview_image", "is_active"]
    list_display_links = ["title"]
    list_filter = ["is_active"]
    ordering = ["order"]
    list_editable = ["order", "is_active"]
    list_per_page = 20
    search_fields = ["title", "subtitle"]
    save_on_top = True

    fieldsets = (
        ("基本資訊", {"fields": ("title", "subtitle")}),
        (
            "圖片與顯示",
            {
                "fields": ("image", "order", "is_active"),
                "description": "排序數字越小越靠前；停用後不會出現在首頁。",
            },
        ),
    )

    @admin.display(description="副標題")
    def subtitle_short(self, obj):
        if obj.subtitle:
            return obj.subtitle[:40] + ("..." if len(obj.subtitle) > 40 else "")
        return "-"

    @admin.display(description="預覽")
    def preview_image(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:48px;border-radius:6px;object-fit:cover">',
                obj.image.url,
            )
        return "-"


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "date_display",
        "location",
        "is_featured",
        "cover_preview",
        "updated_at",
    ]
    list_display_links = ["title"]
    search_fields = ["title", "short_description", "description", "location"]
    search_help_text = "可搜尋：標題、簡介、內容、地點"
    list_filter = ["is_featured", ("date", admin.DateFieldListFilter)]
    ordering = ["-date"]
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ["is_featured"]
    list_per_page = 20
    date_hierarchy = "date"
    save_on_top = True

    fieldsets = (
        ("活動標題", {"fields": ("title", "slug", "short_description")}),
        ("活動詳情", {"fields": ("description", "date", "location")}),
        ("封面與推薦", {"fields": ("cover_image", "is_featured")}),
        (
            "系統時間",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="活動時間", ordering="date")
    def date_display(self, obj):
        now = timezone.now()
        color = "#198754" if obj.date >= now else "#6c757d"
        tag = "即將舉行" if obj.date >= now else "已結束"
        return format_html(
            '<span style="color:{}">{}</span> <small style="color:#6b7280">({})</small>',
            color,
            obj.date.strftime("%Y/%m/%d %H:%M"),
            tag,
        )

    @admin.display(description="封面")
    def cover_preview(self, obj):
        if obj.cover_image:
            return format_html(
                '<img src="{}" style="height:48px;border-radius:6px;object-fit:cover">',
                obj.cover_image.url,
            )
        return "-"

    @admin.action(description="設為推薦活動")
    def mark_featured(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"已將 {updated} 筆活動設為推薦。")

    @admin.action(description="取消推薦活動")
    def unmark_featured(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"已將 {updated} 筆活動取消推薦。")

    actions = ["mark_featured", "unmark_featured"]


class StoryPostAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [
            (value, label)
            for value, label in self.fields["category"].choices
            if value != ""
        ]

    class Meta:
        model = StoryPost
        fields = "__all__"
        widgets = {
            # 避免下拉選單對比不足，改用單選按鈕提高可讀性。
            "category": forms.RadioSelect,
        }


CATEGORY_COLOR = {
    "water_story": ("#166534", "#dcfce7"),
    "usr": ("#1d4ed8", "#dbeafe"),
    "experience": ("#b45309", "#fef3c7"),
    "aiot": ("#6d28d9", "#ede9fe"),
}


@admin.register(StoryPost)
class StoryPostAdmin(admin.ModelAdmin):
    form = StoryPostAdminForm
    list_display = [
        "title",
        "category_badge",
        "is_featured",
        "cover_preview",
        "summary_short",
        "updated_at",
    ]
    list_display_links = ["title"]
    search_fields = ["title", "summary", "content"]
    search_help_text = "可搜尋：標題、摘要、內容"
    list_filter = ["category", "is_featured", "created_at"]
    ordering = ["-created_at"]
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ["is_featured"]
    list_per_page = 20
    date_hierarchy = "created_at"
    save_on_top = True

    fieldsets = (
        ("文章標題", {"fields": ("title", "slug", "category", "summary")}),
        ("文章內容", {"fields": ("content",), "classes": ("wide",)}),
        ("圖片與推薦", {"fields": ("image", "is_featured")}),
        (
            "系統時間",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="分類", ordering="category")
    def category_badge(self, obj):
        fg, bg = CATEGORY_COLOR.get(obj.category, ("#334155", "#e2e8f0"))
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;'
            'border-radius:999px;font-size:.8rem;font-weight:600">{}</span>',
            bg,
            fg,
            obj.get_category_display(),
        )

    @admin.display(description="摘要")
    def summary_short(self, obj):
        return obj.summary[:45] + ("..." if len(obj.summary) > 45 else "")

    @admin.display(description="封面")
    def cover_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:48px;border-radius:6px;object-fit:cover">',
                obj.image.url,
            )
        return "-"

    @admin.action(description="設為推薦文章")
    def mark_featured(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"已將 {updated} 篇文章設為推薦。")

    @admin.action(description="取消推薦文章")
    def unmark_featured(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"已將 {updated} 篇文章取消推薦。")

    @admin.action(description="分類改為 USR 成果")
    def mark_usr(self, request, queryset):
        updated = queryset.update(category="usr")
        self.message_user(request, f"已將 {updated} 篇文章改為 USR 成果。")

    @admin.action(description="分類改為 水井故事")
    def mark_water_story(self, request, queryset):
        updated = queryset.update(category="water_story")
        self.message_user(request, f"已將 {updated} 篇文章改為水井故事。")

    @admin.action(description="分類改為 AIoT 智慧應用")
    def mark_aiot(self, request, queryset):
        updated = queryset.update(category="aiot")
        self.message_user(request, f"已將 {updated} 篇文章改為 AIoT 智慧應用。")

    @admin.action(description="分類改為 體驗活動")
    def mark_experience(self, request, queryset):
        updated = queryset.update(category="experience")
        self.message_user(request, f"已將 {updated} 篇文章改為體驗活動。")

    actions = [
        "mark_featured",
        "unmark_featured",
        "mark_usr",
        "mark_water_story",
        "mark_aiot",
        "mark_experience",
    ]
