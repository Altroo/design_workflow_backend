from chat_ai_assistant.contracts import ChatAIError

ROUTES = {
    "overview": "/dashboard/overview",
    "projects": "/dashboard/projects",
    "board": "/dashboard/board",
    "chat": "/dashboard/chat",
    "notifications": "/dashboard/notifications",
    "reports": "/dashboard/reports/time",
    "team": "/dashboard/team",
    "changelog": "/dashboard/changelog",
}
DETAILS = {"project": "/dashboard/projects/", "task": "/dashboard/tasks/"}


class ChatAINavigationResolver:
    @staticmethod
    def resolve(resource, company_id=1, identifier=None):
        if company_id != 1:
            raise ChatAIError("PERMISSION_DENIED")
        if resource in ROUTES and identifier is None:
            href = ROUTES[resource]
        elif (
            resource in DETAILS
            and type(identifier) is int
            and 0 < identifier <= 2147483647
        ):
            href = DETAILS[resource] + str(identifier)
        else:
            raise ChatAIError("INVALID_ARGUMENTS")
        return {
            "application": "design_workflow",
            "resource": resource,
            "identifier": identifier,
            "company_id": 1,
            "href": href,
        }
