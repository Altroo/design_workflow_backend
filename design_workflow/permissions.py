from rest_framework import permissions


class IsManager(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == "manager")


class IsManagerOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.role == "manager"


def can_mutate_task(user, task) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.role == "manager":
        return True
    return task.current_assignee_id == user.id

