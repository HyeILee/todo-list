from django import forms
from .models import Category, DailyRecord, Task


class AccessibleFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            error_id = f"{self[field_name].auto_id}_errors"
            described_by = field.widget.attrs.get("aria-describedby", "").strip()
            field.widget.attrs["aria-describedby"] = f"{described_by} {error_id}".strip()


class TaskForm(AccessibleFormMixin, forms.ModelForm):
    task_date = forms.DateField(required=False, input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date"}), label="날짜")

    class Meta:
        model = Task
        fields = ["task_date", "category", "title", "details", "is_important"]
        labels = {"category": "카테고리", "title": "해야 할 일", "details": "상세 메모", "is_important": "중요"}
        widgets = {"details": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, limit in (("title", 200), ("details", 2000)):
            self.fields[name].widget.attrs.pop("maxlength", None)
            self.fields[name].widget.attrs["data-max-length"] = str(limit)
        self.fields["category"].queryset = Category.objects.filter(is_archived=False)

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("업무 제목을 입력해 주세요.")
        return title

    def clean_details(self):
        details = self.cleaned_data["details"]
        if len(details) > 2000:
            raise forms.ValidationError("상세 메모는 2,000자까지 입력할 수 있습니다.")
        return details

class CategoryForm(AccessibleFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "color_key"]
        labels = {"name": "카테고리 이름", "color_key": "표시 색상"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.pop("maxlength", None)
        self.fields["name"].widget.attrs["data-max-length"] = "30"

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class DailyRecordForm(AccessibleFormMixin, forms.ModelForm):
    today_story = forms.CharField(required=False, max_length=2000, label="오늘 하루는", widget=forms.Textarea(attrs={"rows": 6, "data-auto-grow": "620"}))
    memorable_sentence = forms.CharField(required=False, max_length=2000, label="기억 속 문장", widget=forms.Textarea(attrs={"rows": 6, "data-auto-grow": "520"}))
    stayed_with_me = forms.CharField(required=False, max_length=2000, label="유독 내 곁에", widget=forms.Textarea(attrs={"rows": 2, "data-auto-grow": "420"}))

    class Meta:
        model = DailyRecord
        fields = ["today_story", "memorable_sentence", "stayed_with_me"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, limit in (("today_story", 2000), ("memorable_sentence", 2000), ("stayed_with_me", 2000)):
            self.fields[name].widget.attrs.pop("maxlength", None)
            self.fields[name].widget.attrs["data-max-length"] = str(limit)
            self.fields[name].widget.attrs["data-note-editor"] = "true"


class TaskFilterForm(AccessibleFormMixin, forms.Form):
    DATE_MODES = [("dated", "날짜가 지정된 업무"), ("all", "날짜 미정 포함"), ("inbox", "보관함만")]
    ORDERINGS = [("status", "상태순 (미정 → X → △ → O)"), ("date_desc", "날짜 최신순"), ("date_asc", "날짜 오래된순"), ("updated_desc", "최근 수정순")]

    date_from = forms.DateField(required=False, label="시작일", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="종료일", widget=forms.DateInput(attrs={"type": "date"}))
    date_mode = forms.ChoiceField(choices=DATE_MODES, required=False, initial="dated", label="날짜")
    categories = forms.ModelMultipleChoiceField(queryset=Category.objects.all(), required=False, label="카테고리", widget=forms.CheckboxSelectMultiple)
    statuses = forms.MultipleChoiceField(choices=Task.Status.choices, required=False, label="상태", widget=forms.CheckboxSelectMultiple)
    important = forms.BooleanField(required=False, label="중요")
    q = forms.CharField(required=False, max_length=200, label="검색어", widget=forms.TextInput(attrs={"placeholder": "제목 또는 상세 메모"}))
    ordering = forms.ChoiceField(choices=ORDERINGS, required=False, initial="status", label="정렬")

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("시작일은 종료일보다 늦을 수 없습니다.")
        return cleaned


class RecordFilterForm(AccessibleFormMixin, forms.Form):
    SECTIONS = [
        ("", "전체"),
        ("today_story", "오늘 하루는"),
        ("memorable_sentence", "기억 속 문장"),
        ("stayed_with_me", "유독 내 곁에"),
    ]

    date_from = forms.DateField(required=False, label="시작일", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="종료일", widget=forms.DateInput(attrs={"type": "date"}))
    section = forms.ChoiceField(required=False, choices=SECTIONS, label="기록 항목")
    q = forms.CharField(required=False, max_length=200, label="검색어", widget=forms.TextInput(attrs={"placeholder": "기록 내용 검색"}))

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("시작일은 종료일보다 늦을 수 없습니다.")
        return cleaned
