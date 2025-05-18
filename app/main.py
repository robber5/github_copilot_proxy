import os
import json
import uuid
import requests
from datetime import datetime, timezone
from aiohttp import web, ClientSession
from pydantic import BaseModel
from pathlib import Path
from requests.exceptions import RequestException

class CopilotClientError(Exception):
    """Base exception for all client-related errors."""


class APIError(CopilotClientError):
    """Raised when API calls fail."""


class AuthenticationError(CopilotClientError):
    """Raised when authentication fails."""

class APIEndpoints:
    TOKEN = "https://api.github.com/copilot_internal/v2/token"

class Headers:
    AUTH = {
        "editor-plugin-version": "copilotcli/1.0.0",
        "user-agent": "copilotcli/1.0.0",
        "editor-version": "vscode/1.83.0",
    }

class HostsData(BaseModel):
    github_oauth_token: str

    @classmethod
    def from_file(cls, file_path: Path) -> "HostsData":
        hosts_file = Path(file_path)
        hosts_data = json.loads(hosts_file.read_text())
        for key, value in hosts_data.items():
            if "github.com" in key:
                return cls(github_oauth_token=value["oauth_token"])
            
class CopilotToken(BaseModel):
    """
    Represents a GitHub Copilot authentication token and its associated metadata.
    """

    token: str
    expires_at: int
    refresh_in: int
    endpoints: dict[str, str]
    tracking_id: str
    sku: str

    annotations_enabled: bool
    chat_enabled: bool
    chat_jetbrains_enabled: bool
    code_quote_enabled: bool
    codesearch: bool
    copilotignore_enabled: bool
    individual: bool
    prompt_8k: bool
    snippy_load_test_enabled: bool
    xcode: bool
    xcode_chat: bool

    public_suggestions: str
    telemetry: str
    # enterprise_list: list[int]

    code_review_enabled: bool

class GithubCopilotClient:
    """
    Client for interacting with GitHub Copilot's API.
    """

    def __init__(self) -> None:
        self._oauth_token: str | None = None
        self._copilot_token: CopilotToken | None = None
        self._machine_id: str = str(uuid.uuid4())
        self._session_id: str = ""

        self._load_cached_token()

    def _load_cached_token(self) -> None:
        """
        Attempts to load a cached Copilot token.
        """
        cache_path = Path("/tmp/copilot_token.json")
        if cache_path.exists():
            try:
                token_data = json.loads(cache_path.read_text())
                self._copilot_token = CopilotToken(**token_data)
            except (json.JSONDecodeError, TypeError):
                cache_path.unlink(missing_ok=True)

    def _load_oauth_token(self) -> str:
        """Loads the OAuth token from the GitHub Copilot configuration."""
        config_dir = os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")

        files = [
            Path(config_dir) / "github-copilot" / "hosts.json",
            Path(config_dir) / "github-copilot" / "apps.json",
        ]

        for file in files:
            if file.exists():
                try:
                    host_data = HostsData.from_file(file)
                    if host_data and host_data.github_oauth_token:
                        return host_data.github_oauth_token
                except (FileNotFoundError, json.JSONDecodeError, KeyError):
                    raise AuthenticationError("GitHub Copilot configuration not found or invalid.")

        raise AuthenticationError("OAuth token not found in GitHub Copilot configuration.")

    def _get_oauth_token(self) -> str:
        """
        Gets or loads the OAuth token.
        """

        if not self._oauth_token:
            self._oauth_token = self._load_oauth_token()
        return self._oauth_token

    def _refresh_copilot_token(self) -> None:
        """Refreshes the Copilot token using the OAuth token."""
        self._session_id = f"{uuid.uuid4()}{int(datetime.now(timezone.utc).timestamp() * 1000)}"

        headers = {
            "Authorization": f"token {self._get_oauth_token()}",
            "Accept": "application/json",
            **Headers.AUTH,
        }

        try:
            response = requests.get(APIEndpoints.TOKEN, headers=headers, timeout=10)
            response.raise_for_status()
            token_data = response.json()

            self._copilot_token = CopilotToken(**token_data)

            # Cache the token
            cache_path = Path("/tmp/copilot_token.json")
            _ = cache_path.write_text(json.dumps(token_data))

        except RequestException as e:
            raise APIError(f"Failed to refresh Copilot token: {str(e)}") from e

    def _ensure_valid_token(self) -> None:
        """
        Ensures a valid Copilot token is available.
        """

        current_time = int(datetime.now(timezone.utc).timestamp())

        if not self._copilot_token or current_time >= self._copilot_token.expires_at:
            self._refresh_copilot_token()

        if not self._copilot_token:
            raise AuthenticationError("Failed to obtain Copilot token")

    def get_headers(self):
        self._ensure_valid_token()

        return {
            "Content-Type": "application/json",
            "x-request-id": str(uuid.uuid4()),
            "vscode-machineid": self._machine_id,
            "vscode-sessionid": self._session_id,
            "Authorization": f"Bearer {self._copilot_token.token}",
            "Copilot-Integration-Id": "vscode-chat",
            "openai-organization": "github-copilot",
            "openai-intent": "conversation-panel",
            **Headers.AUTH,
        }
    
copilotclient = GithubCopilotClient()
token = os.getenv("TOKEN", "your_default_token")  # 从环境变量中获取 token，或使用默认值

async def proxy_handler(request):
    # 添加 Bearer Token 认证
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return web.Response(status=401, text="Unauthorized")

    if auth_header[len("Bearer "):].strip() != token:
        return web.Response(status=401, text="Unauthorized")
    
    # 获取目标路径
    path = request.match_info.get("path", "")
    target_url = f"https://api.githubcopilot.com/{path}"  # 替换为目标地址

    # 获取请求方法、头部和数据
    method = request.method
    # headers = {key: value for key, value in request.headers.items()}
    headers = copilotclient.get_headers()
    # headers["Host"] = "api.githubcopilot.com"  # 设置 Host 头部
    body = await request.read()

    # 根据 Content-Type 动态处理 body
    content_type = headers.get("Content-Type", "")
    request_kwargs = {"method": method, "url": target_url, "headers": headers}

    if "application/json" in content_type:
        request_kwargs["json"] = await request.json()
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        request_kwargs["data"] = body
    else:
        request_kwargs["data"] = body  # 默认处理为原始数据

    async with ClientSession() as session:
        async with session.request(**request_kwargs) as response:
            # 检查是否需要流式传输
            if request_kwargs["json"]['stream']:
                async def stream_response():
                    async for chunk in response.content.iter_chunked(1024):
                        yield chunk

                stream_resp = web.StreamResponse(
                    status=response.status,
                    headers=response.headers,
                )
                await stream_resp.prepare(request)  # Await the prepare coroutine
                async for chunk in stream_response():
                    await stream_resp.write(chunk)  # Write chunks to the response
                return stream_resp
            
            # 处理非流式响应
            response_json = await response.json()
            print(response_json)
            return web.json_response(response_json, status=response.status)


# 创建 aiohttp 应用
app = web.Application()
app.router.add_route("*", "/{path:.*}", proxy_handler)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=80)