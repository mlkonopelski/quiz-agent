import type {
  AuthSessionResponse,
  CommandEnvelope,
  CreateSessionResponse,
  WorkflowSnapshot,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message = "Authentication required.") {
    super(401, message);
    this.name = "UnauthorizedError";
  }
}

export class QuizApiClient {
  async login(email: string, password: string): Promise<AuthSessionResponse> {
    return this.requestJson<AuthSessionResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  async logout(): Promise<void> {
    await this.requestJson("/auth/logout", { method: "POST" });
  }

  async getCurrentUser(): Promise<AuthSessionResponse> {
    return this.requestJson<AuthSessionResponse>("/auth/me");
  }

  async createSession(): Promise<string> {
    const payload = await this.requestJson<CreateSessionResponse>("/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return payload.workflow_id;
  }

  async sendCommand(
    workflowId: string,
    command: CommandEnvelope,
  ): Promise<void> {
    await this.requestJson(`/sessions/${workflowId}/commands`, {
      method: "POST",
      body: JSON.stringify(command),
    });
  }

  async getActiveSession(): Promise<string | null> {
    const payload = await this.requestJson<CreateSessionResponse | null>(
      "/sessions/active",
    );
    return payload?.workflow_id ?? null;
  }

  async getSnapshot(workflowId: string): Promise<WorkflowSnapshot> {
    return this.requestJson<WorkflowSnapshot>(`/sessions/${workflowId}/snapshot`);
  }

  private async requestJson<T = Record<string, unknown>>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    const response = await fetch(path, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
      ...init,
    });

    if (response.status === 401) {
      throw new UnauthorizedError(await this.extractError(response));
    }
    if (!response.ok) {
      throw new ApiError(response.status, await this.extractError(response));
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  private async extractError(response: Response): Promise<string> {
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        return payload.detail;
      }
      return JSON.stringify(payload);
    } catch {
      return (await response.text()) || response.statusText;
    }
  }
}

export const apiClient = new QuizApiClient();
