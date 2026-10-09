from enum import Enum
from typing import List, Optional
from app.domain.employee import SeniorityLevel, DepartmentType, EmployeeProfile


class StandardRole(str, Enum):
    HEAD_OF_SALES = "HEAD_OF_SALES"
    HEAD_OF_HR = "HEAD_OF_HR"
    CHIEF_ACCOUNTANT = "CHIEF_ACCOUNTANT"
    GENERAL_EMPLOYEE = "GENERAL_EMPLOYEE"
    EXECUTIVE_DIRECTOR = "EXECUTIVE_DIRECTOR"


class RoleClassifier:
    """Xác định vai trò kinh doanh chuẩn hóa từ chức danh, phòng ban và phân quyền Odoo."""

    @staticmethod
    def classify(profile: EmployeeProfile) -> StandardRole:
        title = (profile.job_title or "").lower()
        dept = (profile.department or "").lower()
        roles_set = set(r.lower() for r in profile.roles)

        # 1. Executive check
        if any(r in roles_set for r in ["admin", "ceo", "board"]):
            if "sales" in dept or "kinh doanh" in dept:
                return StandardRole.HEAD_OF_SALES
            if "hr" in dept or "nhân sự" in dept:
                return StandardRole.HEAD_OF_HR
            if "kế toán" in dept or "tài chính" in dept or "accounting" in dept:
                return StandardRole.CHIEF_ACCOUNTANT
            return StandardRole.EXECUTIVE_DIRECTOR

        # 2. Head of Sales check
        sales_keywords = ["trưởng phòng kinh doanh", "head of sales", "sales manager", "giám đốc kinh doanh", "sales director"]
        if any(k in title for k in sales_keywords) or (
            ("kinh doanh" in dept or "sales" in dept) and profile.seniority in [SeniorityLevel.HEAD, SeniorityLevel.EXECUTIVE]
        ) or "sales_manager" in roles_set:
            return StandardRole.HEAD_OF_SALES

        # 3. Head of HR check
        hr_keywords = ["trưởng phòng nhân sự", "head of hr", "hr manager", "giám đốc nhân sự", "hr director"]
        if any(k in title for k in hr_keywords) or (
            ("nhân sự" in dept or "hr" in dept) and profile.seniority in [SeniorityLevel.HEAD, SeniorityLevel.EXECUTIVE]
        ) or "hr_manager" in roles_set:
            return StandardRole.HEAD_OF_HR

        # 4. Chief Accountant check
        acc_keywords = ["kế toán trưởng", "chief accountant", "accounting manager", "giám đốc tài chính", "cfo"]
        if any(k in title for k in acc_keywords) or (
            ("kế toán" in dept or "tài chính" in dept or "accounting" in dept) and profile.seniority in [SeniorityLevel.HEAD, SeniorityLevel.EXECUTIVE]
        ) or "account_manager" in roles_set:
            return StandardRole.CHIEF_ACCOUNTANT

        # Default fallback
        return StandardRole.GENERAL_EMPLOYEE
