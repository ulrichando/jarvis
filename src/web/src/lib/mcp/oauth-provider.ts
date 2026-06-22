import "server-only";
import type { OAuthClientProvider } from "@modelcontextprotocol/sdk/client/auth.js";
import type {
  OAuthClientInformation,
  OAuthClientInformationFull,
  OAuthClientMetadata,
  OAuthTokens,
} from "@modelcontextprotocol/sdk/shared/auth.js";
import { patchPending, saveServerAuth, type Transport } from "./oauth-store";
import { upsertOAuthServer } from "./store";

// An SDK OAuthClientProvider backed by oauth-store. The SDK drives the whole
// OAuth 2.1 + PKCE + dynamic-client-registration dance and calls back into these
// methods; we persist the bits we need to resume in the callback / refresh
// later. `redirectToAuthorization` does NOT redirect (we're server-side) — it
// captures the URL so the API route can hand it to the browser.
//
// One instance handles both phases:
//   - start:   no seed → SDK does discovery+DCR, saveClientInformation +
//              saveCodeVerifier write into pending[state], redirect URL captured.
//   - finish/connect: seeded from the stored ServerAuth/PendingAuth so the SDK
//              can exchange the code or refresh the token.
export class FileOAuthProvider implements OAuthClientProvider {
  capturedAuthUrl: URL | undefined;
  private _clientInfo?: OAuthClientInformationFull;
  private _tokens?: OAuthTokens;
  private _verifier?: string;

  constructor(
    private readonly opts: {
      name: string;
      state: string;
      url: string;
      transport: Transport;
      redirectUri: string;
      seed?: {
        clientInfo?: OAuthClientInformationFull;
        tokens?: OAuthTokens;
        codeVerifier?: string;
      };
    },
  ) {
    this._clientInfo = opts.seed?.clientInfo;
    this._tokens = opts.seed?.tokens;
    this._verifier = opts.seed?.codeVerifier;
  }

  get redirectUrl(): string {
    return this.opts.redirectUri;
  }

  get clientMetadata(): OAuthClientMetadata {
    return {
      client_name: "JARVIS",
      redirect_uris: [this.opts.redirectUri],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none", // public client + PKCE
    };
  }

  state(): string {
    return this.opts.state;
  }

  clientInformation(): OAuthClientInformation | undefined {
    return this._clientInfo;
  }

  async saveClientInformation(info: OAuthClientInformationFull): Promise<void> {
    this._clientInfo = info;
    await patchPending(this.opts.state, { clientInfo: info });
  }

  tokens(): OAuthTokens | undefined {
    return this._tokens;
  }

  async saveTokens(tokens: OAuthTokens): Promise<void> {
    this._tokens = tokens;
    // Persist completed auth keyed by server name — used to seed refresh on
    // later connects. Requires the client registration to be known by now.
    if (this._clientInfo) {
      await saveServerAuth(this.opts.name, {
        url: this.opts.url,
        transport: this.opts.transport,
        redirectUri: this.opts.redirectUri,
        clientInfo: this._clientInfo,
        tokens,
        obtainedAt: Date.now(),
      });
    }
    // Mirror the (possibly refreshed) access token into mcp.json so the voice
    // agent — which only reads the static header — stays current too.
    if (tokens.access_token) {
      await upsertOAuthServer({
        name: this.opts.name,
        url: this.opts.url,
        transport: this.opts.transport,
        accessToken: tokens.access_token,
      });
    }
  }

  redirectToAuthorization(url: URL): void {
    this.capturedAuthUrl = url;
  }

  async saveCodeVerifier(verifier: string): Promise<void> {
    this._verifier = verifier;
    await patchPending(this.opts.state, { codeVerifier: verifier });
  }

  codeVerifier(): string {
    if (!this._verifier) throw new Error("no PKCE code verifier in this session");
    return this._verifier;
  }
}
