from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from apps.core.models import Department


# Create your models here.
class CourseTemplate(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="course_templates")
    code = models.CharField(max_length=10,)
    name = models.CharField(max_length=100,)
    credit = models.PositiveSmallIntegerField()
    description = models.TextField(blank=True, null=True)
    target_class_year = models.PositiveSmallIntegerField(
        "Target Class Year",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(6)],
        help_text="Target class year for this course (1-6). Helps filter students during enrollment."
    )
    
    class Meta:
        unique_together = ("department", "code")
        ordering = ["department", "code"]

    def __str__(self):
        return f"{self.department.code} - {self.code}: {self.name}"

    def get_full_code(self):
        return f"{self.department.code} - {self.code}"

    def clean(self):
        """Normalize code and name fields before saving."""
        if self.code:
            self.code = self.code.strip().upper()
        if not self.code:
            raise ValidationError({"code": "Course code cannot be empty."})
        
        if self.name:
            self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Course name cannot be empty."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
class CourseInstance(models.Model):
    course_template = models.ForeignKey(CourseTemplate, on_delete=models.CASCADE, related_name="instances")
    semester = models.CharField(max_length=20, help_text="Ex: Fall 2024")
    year = models.PositiveSmallIntegerField(help_text="Ex: 2024")
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="taught_courses",
        limit_choices_to=Q(role="INSTRUCTOR") | Q(role="DEPARTMENT_HEAD"),
    )
    students = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="enrolled_courses",
        limit_choices_to=Q(role="STUDENT"),
    )
    is_active = models.BooleanField(default=True)
    class Meta:
        unique_together = ("course_template", "semester", "year")
        ordering = ["-year", "semester"]
    def __str__(self):
        return f"{self.course_template} - {self.semester} {self.year}"
    def get_full_code(self):
        return f"{self.course_template.get_full_code()} - {self.semester} {self.year}"




class LearningOutcome(models.Model):
    LO_PREFIX = "LO-"
    course_template = models.ForeignKey(
        CourseTemplate, on_delete=models.CASCADE, related_name="learning_outcomes"
    )
    code = models.CharField(max_length=10, help_text="The code of the learning outcome")
    description = models.TextField(help_text="The description of the learning outcome")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Learning Outcome"
        verbose_name_plural = "Learning Outcomes"
        unique_together = ("course_template", "code")
        ordering = ["course_template", "code"]

    def __str__(self):
        return f"{self.course_template.get_full_code()} - {self.code}"

    def get_full_code(self):
        """Full LO code: CSE-CS101-LO-1"""
        return f"{self.course_template.get_full_code()}-{self.code}"

    def clean(self):
        if self.code:
            cleaned_code = self.code.strip().upper()
            if cleaned_code.isdigit():
                self.code = f"{self.LO_PREFIX}{cleaned_code}"
            elif not cleaned_code.startswith(self.LO_PREFIX):
                raise ValidationError(f"Learning Outcome code must start with '{self.LO_PREFIX}' followed by a number.")
            else:
                self.code = cleaned_code
        else:
            raise ValidationError("Learning Outcome code cannot be empty.")
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

class Assessment(models.Model):

    class AssessmentType(models.TextChoices):
        MIDTERM = "MIDTERM", "Midterm Exam"
        FINAL = "FINAL", "Final Exam"
        PROJECT = "PROJECT", "Project"
        HOMEWORK = "HOMEWORK", "Homework"
        QUIZ = "QUIZ", "Quiz"
        LAB = "LAB", "Lab"

    course_instance = models.ForeignKey(
            CourseInstance,
            on_delete=models.CASCADE,
            related_name="assessments",
         )
    name = models.CharField(
            max_length=50, help_text="Ex: Midterm 1, Final Exam, Project 1"
        )
    assessment_type = models.CharField(
            max_length=20,choices=AssessmentType.choices)
    max_score = models.DecimalField(
            max_digits=6,
            decimal_places=2,
            default=100,
            validators=[MinValueValidator(0)],
        )
    weight = models.DecimalField(
            "Contribution weight to final grade (%)",
            max_digits=5,
            decimal_places=2,
            validators=[MinValueValidator(0), MaxValueValidator(100)],
            help_text="Weight of this assessment towards the final course grade ex: 20 for 20%",)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
            verbose_name = "Assessment"
            verbose_name_plural = "Assessments"
            ordering = ["course_instance", "assessment_type", "name"]

    def clean(self):
        """
        Validate that total weights don't exceed 100%.
        Note: When used in inline formsets, this validation may be incomplete
        as it only sees existing assessments, not other forms in the formset.
        The formset's clean() method provides complete validation.
        """
        from django.db.models import Sum
        has_parent = False
        try:
            if self.course_instance and self.course_instance.pk:
                has_parent = True
        except (AttributeError, ValueError, models.ObjectDoesNotExist):
            pass  

        # Only validate if we have a parent instance
        # For new instances created via inline formsets, formset validation will handle it
        if self.weight and has_parent and self.pk:
            # Only validate for existing assessments being edited individually
            # (not as part of an inline formset)
            existing_total = self.course_instance.assessments.exclude(
                pk=self.pk
            ).aggregate(total=Sum('weight'))['total'] or 0
            
            new_total = existing_total + self.weight
            if new_total > 100:
                raise ValidationError({
                    'weight': f"Total weight would be {new_total}%. Cannot exceed 100%. Current total: {existing_total}%."
                })

    def __str__(self):
        try:
            return f"{self.course_instance.get_full_code()} - {self.name}"
        except (AttributeError, models.ObjectDoesNotExist):
            return f"Unassigned Assessment - {self.name}"


class AssessmentToLOContribution(models.Model):
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="assessment_lo_contributions",
    )
    learning_outcome = models.ForeignKey(
        LearningOutcome,
        on_delete=models.CASCADE,
        related_name="assessment_contributions",
    )

    weight = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        verbose_name = "Assessment to Learning Outcome Contribution"
        verbose_name_plural = "Assessment to Learning Outcome Contributions"
        unique_together = ("assessment", "learning_outcome")

    def __str__(self):
        return f"{self.assessment} -> {self.learning_outcome.code} (Weight: {self.weight})"
    def clean(self):
        if self.learning_outcome.course_template != self.assessment.course_instance.course_template:
            raise ValidationError(
                "Learning Outcome must belong to the same course as the Assessment."
            )
        

class LOtoPOContribution(models.Model):
    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        DECLINED = "DECLINED", "Declined"

    learning_outcome = models.ForeignKey(
        LearningOutcome,
        on_delete=models.CASCADE,
        related_name="po_contributions",
    )
    program_outcome = models.ForeignKey(
        "core.ProgramOutcome",
        on_delete=models.CASCADE,
        related_name="lo_contributions",
    )
    weight = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
        help_text="Current approval status of this LO-PO contribution"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_lo_po_contributions",
        limit_choices_to=Q(role="DEPARTMENT_HEAD"),
        help_text="Department head who approved or declined this contribution"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Reason for declining this contribution"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Learning Outcome to Program Outcome Contribution"
        verbose_name_plural = "Learning Outcome to Program Outcome Contributions"
        unique_together = ("learning_outcome", "program_outcome")

    def __str__(self):
        return f"{self.learning_outcome.code} -> {self.program_outcome.code} (Weight: {self.weight})"
    def clean(self):
        if self.learning_outcome.course_template.department != self.program_outcome.department:
            raise ValidationError(
                "Program Outcome must belong to the same department as the Learning Outcome."
            )
    
    def approve(self, user):
        """Approve this LO-PO contribution."""
        if not user.is_department_head():
            raise PermissionError("Only department heads can approve LO-PO contributions.")
        self.approval_status = self.ApprovalStatus.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.decline_reason = None
        self.save()
    
    def decline(self, user, reason=None):
        """Decline this LO-PO contribution."""
        if not user.is_department_head():
            raise PermissionError("Only department heads can decline LO-PO contributions.")
        self.approval_status = self.ApprovalStatus.DECLINED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.decline_reason = reason
        self.save()
    
    def reset_to_pending(self, user):
        """Reset contribution to pending status (for department head override)."""
        if not user.is_department_head():
            raise PermissionError("Only department heads can reset LO-PO contributions.")
        self.approval_status = self.ApprovalStatus.PENDING
        self.approved_by = None
        self.approved_at = None
        self.decline_reason = None
        self.save()
    
    @property
    def is_approved(self):
        """Backward compatibility property."""
        return self.approval_status == self.ApprovalStatus.APPROVED
    
    @property
    def is_declined(self):
        """Check if contribution is declined."""
        return self.approval_status == self.ApprovalStatus.DECLINED
    
    @property
    def is_pending(self):
        """Check if contribution is pending."""
        return self.approval_status == self.ApprovalStatus.PENDING

class CourseAnnouncement(models.Model):
    course_instance = models.ForeignKey(
        "courses.CourseInstance",
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    title = models.CharField(max_length=150)
    message = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.course_instance.get_full_code()} - {self.title}"


class CourseAnnouncementReadReceipt(models.Model):
    """Tracks which users have read which course announcements."""
    
    announcement = models.ForeignKey(
        CourseAnnouncement,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="course_announcement_reads",
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("announcement", "user")
        verbose_name = "Course Announcement Read Receipt"
        verbose_name_plural = "Course Announcement Read Receipts"

    def __str__(self):
        return f"{self.user} read {self.announcement}"