from django.contrib import admin, messages
from django.forms.models import BaseInlineFormSet
from django.forms import ModelForm
from django.core.exceptions import ValidationError
from django.db.models import Sum
from .models import (
    CourseTemplate,
    CourseInstance,
    LearningOutcome,
    Assessment,
    AssessmentToLOContribution,
    LOtoPOContribution,
    CourseAnnouncement,
    CourseAnnouncementReadReceipt,
)

# -------------------
# INLINES
# -------------------

class LearningOutcomeInline(admin.TabularInline):
    model = LearningOutcome
    extra = 1
    fields = ["code", "description"]


class AssessmentForm(ModelForm):
    """Custom form that skips model-level clean() to avoid conflicts with formset validation."""
    
    class Meta:
        model = Assessment
        fields = '__all__'
    
    def _post_clean(self):
        """
        Override _post_clean to skip model's clean() method.
        The formset's clean() will handle total weight validation.
        """
        # Ensure instance exists
        if self.instance is None:
            self.instance = self._meta.model()
        
        # Store original clean method
        if hasattr(self.instance, 'clean'):
            original_clean = self.instance.clean
        else:
            original_clean = None
        
        # Temporarily replace clean with a no-op on this instance only
        def skip_clean():
            pass
        
        self.instance.clean = skip_clean
        
        try:
            # Call parent's _post_clean (which will call the disabled clean)
            super()._post_clean()
        finally:
            # Restore original clean method
            if original_clean:
                self.instance.clean = original_clean
            elif hasattr(self.instance.__class__, 'clean'):
                # Restore from class if instance method was removed
                delattr(self.instance, 'clean')


class AssessmentInlineFormSet(BaseInlineFormSet):
    """Custom formset to validate total assessment weights don't exceed 100%."""
    
    def clean(self):
        """Validate that total weights of all assessments don't exceed 100%."""
        if any(self.errors):
            # Don't validate if there are already errors
            return
        
        # Get the parent instance (CourseInstance)
        if self.instance and self.instance.pk:
            course_instance = self.instance
        else:
            # For new instances, we can't validate yet as course_instance doesn't exist
            # But we can still check the forms in the formset
            course_instance = None
        
        # Calculate total weight from all forms in the formset
        formset_total = 0
        forms_to_check = []
        existing_ids = set()
        
        for form in self.forms:
            # Skip empty forms (not filled out) - check if form has any meaningful data
            if not form.cleaned_data:
                continue
            
            # Skip deleted forms
            if form.cleaned_data.get('DELETE', False):
                # If it's an existing assessment being deleted, track its ID
                if form.instance and form.instance.pk:
                    existing_ids.add(form.instance.pk)
                continue
            
            # Only count forms that have a weight value
            weight = form.cleaned_data.get('weight')
            if weight is not None and weight != '':
                try:
                    weight_value = float(weight)
                    formset_total += weight_value
                    forms_to_check.append(form)
                    # Track existing assessment IDs that are being edited (not deleted)
                    if form.instance and form.instance.pk:
                        existing_ids.add(form.instance.pk)
                except (ValueError, TypeError):
                    # Skip invalid weight values
                    continue
        
        # If editing existing instance, also include existing assessments not in formset
        existing_total = 0
        if course_instance and course_instance.pk:
            # Sum weights of existing assessments not being edited/deleted in this formset
            existing_total = course_instance.assessments.exclude(
                pk__in=existing_ids
            ).aggregate(total=Sum('weight'))['total'] or 0
        
        # Calculate final total
        total_weight = formset_total + float(existing_total)
        
        # Check if total is exactly 100%
        if total_weight != 100:
            error_msg = f"Total weight is {total_weight:.2f}%. Must be exactly 100%."
            
            # Add error to all forms with weight fields
            for form in forms_to_check:
                form.add_error('weight', error_msg)
            
            raise ValidationError(error_msg)


class AssessmentInline(admin.TabularInline):
    model = Assessment
    extra = 1
    fields = ["name", "assessment_type", "max_score", "weight"]
    form = AssessmentForm
    formset = AssessmentInlineFormSet


class AssessmentToLOInline(admin.TabularInline):
    model = AssessmentToLOContribution
    extra = 1
    fields = ["learning_outcome", "weight"]


class LOtoPOInline(admin.TabularInline):
    model = LOtoPOContribution
    extra = 1
    fields = ["program_outcome", "weight", "is_approved", "approved_by", "approved_at"]
    readonly_fields = ["is_approved", "approved_by", "approved_at"]


# -------------------
# ADMINS
# -------------------

@admin.register(CourseTemplate)
class CourseTemplateAdmin(admin.ModelAdmin):
    list_display = ("department", "code", "name", "credit", "target_class_year")
    list_filter = ("department", "target_class_year")
    search_fields = ("code", "name", "department__code", "department__name")
    inlines = [LearningOutcomeInline]


@admin.register(CourseInstance)
class CourseInstanceAdmin(admin.ModelAdmin):
    list_display = ("get_full_code", "semester", "year", "instructor", "is_active")
    list_filter = ("course_template__department", "semester", "year", "is_active")
    search_fields = ("course_template__code", "course_template__name", "instructor__email")
    filter_horizontal = ("students",)
    inlines = [AssessmentInline]

    def get_full_code(self, obj):
        return obj.get_full_code()
    get_full_code.short_description = "Course Instance"

    def get_form(self, request, obj=None, **kwargs):
        # Store the object being edited on the request so we can access it 
        # in formfield_for_manytomany
        request._obj_ = obj
        return super().get_form(request, obj, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "students":
            obj = getattr(request, "_obj_", None)
            User = db_field.remote_field.model
            department = None
            
            if obj:
                # Editing existing instance - use the course's department
                department = obj.course_template.department
            else:
                # Creating new instance - try to get course_template from POST data
                course_template_id = request.POST.get('course_template')
                if course_template_id:
                    try:
                        from apps.courses.models import CourseTemplate
                        course_template = CourseTemplate.objects.get(pk=course_template_id)
                        department = course_template.department
                    except CourseTemplate.DoesNotExist:
                        pass
                
                # Fallback to user's department if no course_template selected yet
                if not department and hasattr(request.user, 'department') and request.user.department:
                    department = request.user.department
            
            if department:
                kwargs["queryset"] = User.objects.filter(role="STUDENT", department=department)
            else:
                # Superuser without department: show all students
                kwargs["queryset"] = User.objects.filter(role="STUDENT")
                
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("id","name", "course_instance", "assessment_type", "max_score", "weight")
    list_filter = ("assessment_type", "course_instance__course_template__department")
    search_fields = ("name", "course_instance__course_template__code", "course_instance__course_template__name")
    inlines = [AssessmentToLOInline]


@admin.register(LearningOutcome)
class LearningOutcomeAdmin(admin.ModelAdmin):
    list_display = ("code", "course_template", "get_department", "description")
    list_filter = ("course_template__department",)
    search_fields = ("code", "description", "course_template__code", "course_template__name")
    inlines = [LOtoPOInline]

    def get_department(self, obj):
        return obj.course_template.department
    get_department.short_description = "Department"


@admin.register(AssessmentToLOContribution)
class AssessmentToLOContributionAdmin(admin.ModelAdmin):
    list_display = ("assessment", "learning_outcome", "weight")
    list_filter = ("assessment__course_instance__course_template__department",)
    search_fields = ("assessment__name", "learning_outcome__code")


@admin.register(LOtoPOContribution)
class LOtoPOContributionAdmin(admin.ModelAdmin):
    list_display = ("learning_outcome", "program_outcome", "weight", "approval_status", "approved_by", "approved_at")
    list_filter = (
        "approval_status",
        "learning_outcome__course_template__department",
        "program_outcome__department",
    )
    search_fields = ("learning_outcome__code", "program_outcome__code")
    actions = ["approve_mappings"]

    def get_form(self, request, obj=None, **kwargs):
        request._obj_ = obj
        return super().get_form(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        obj = getattr(request, "_obj_", None)
        
        if db_field.name == "program_outcome":
            from apps.core.models import ProgramOutcome
            
            if obj and obj.learning_outcome:
                # Editing: filter by LO's department
                kwargs["queryset"] = ProgramOutcome.objects.filter(
                    department=obj.learning_outcome.course_template.department
                )
            else:
                # Creating: try to get LO from POST, fallback to user dept
                lo_id = request.POST.get('learning_outcome')
                if lo_id:
                    try:
                        lo = LearningOutcome.objects.get(pk=lo_id)
                        kwargs["queryset"] = ProgramOutcome.objects.filter(
                            department=lo.course_template.department
                        )
                    except LearningOutcome.DoesNotExist:
                        pass
                elif hasattr(request.user, 'department') and request.user.department:
                    kwargs["queryset"] = ProgramOutcome.objects.filter(
                        department=request.user.department
                    )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def approve_mappings(self, request, queryset):
        user = request.user
        if not hasattr(user, "is_department_head") or not user.is_department_head():
            self.message_user(
                request,
                "Only department heads can approve mappings!",
                level=messages.ERROR,
            )
            return

        count = 0
        for mapping in queryset:
            mapping.approve(user)
            count += 1

        self.message_user(request, f"{count} mapping approved.", level=messages.SUCCESS)

    approve_mappings.short_description = "Approve selected LO-PO mappings"


@admin.register(CourseAnnouncement)
class CourseAnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "course_instance", "created_by", "created_at")
    list_filter = ("course_instance__course_template__department", "created_at")
    search_fields = ("title", "message", "course_instance__course_template__code")
    ordering = ("-created_at",)


@admin.register(CourseAnnouncementReadReceipt)
class CourseAnnouncementReadReceiptAdmin(admin.ModelAdmin):
    list_display = ("announcement", "user", "read_at")
    list_filter = ("announcement__course_instance__course_template__department", "read_at")
    search_fields = ("announcement__title", "user__email")
    ordering = ("-read_at",)