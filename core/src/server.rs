use tonic::{transport::Server, Request, Response, Status};
use std::process::Command;

pub mod jarvis_proto {
    tonic::include_proto!("jarvis");
}

use jarvis_proto::jarvis_brain_server::{JarvisBrain, JarvisBrainServer};
use jarvis_proto::*;

#[derive(Debug, Default)]
pub struct JarvisService;

#[tonic::async_trait]
impl JarvisBrain for JarvisService {
    async fn query(
        &self,
        request: Request<QueryRequest>,
    ) -> Result<Response<QueryResponse>, Status> {
        let req = request.into_inner();
        tracing::info!("gRPC Query: {}", req.text);

        // Forward to Python brain via HTTP API
        let response = forward_to_brain(&req.text).await
            .map_err(|e| Status::internal(format!("Brain error: {}", e)))?;

        Ok(Response::new(QueryResponse {
            text: response,
            intent: "".to_string(),
            actions: vec![],
        }))
    }

    async fn voice_query(
        &self,
        _request: Request<tonic::Streaming<AudioChunk>>,
    ) -> Result<Response<QueryResponse>, Status> {
        Err(Status::unimplemented("Voice query — use WebSocket instead"))
    }

    async fn execute(
        &self,
        request: Request<ExecuteRequest>,
    ) -> Result<Response<ExecuteResponse>, Status> {
        let req = request.into_inner();
        tracing::info!("gRPC Execute: {}", req.command);

        // Run command directly
        let output = Command::new("bash")
            .arg("-c")
            .arg(&req.command)
            .output()
            .map_err(|e| Status::internal(format!("Command error: {}", e)))?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        Ok(Response::new(ExecuteResponse {
            output: if stdout.is_empty() { stderr } else { stdout },
            exit_code: output.status.code().unwrap_or(-1),
            success: output.status.success(),
        }))
    }

    async fn get_history(
        &self,
        _request: Request<HistoryRequest>,
    ) -> Result<Response<HistoryResponse>, Status> {
        Ok(Response::new(HistoryResponse { turns: vec![] }))
    }

    type QueryStreamStream =
        tokio_stream::wrappers::ReceiverStream<Result<StreamChunk, Status>>;

    async fn query_stream(
        &self,
        _request: Request<QueryRequest>,
    ) -> Result<Response<Self::QueryStreamStream>, Status> {
        Err(Status::unimplemented("Use WebSocket for streaming"))
    }
}

/// Forward a query to the Python brain via its HTTP API
async fn forward_to_brain(text: &str) -> Result<String, Box<dyn std::error::Error>> {
    let client = reqwest::Client::new();
    let resp = client
        .get("http://localhost:8765/api/mesh/task")
        .json(&serde_json::json!({"task": text}))
        .send()
        .await?;

    let data: serde_json::Value = resp.json().await?;
    Ok(data["result"].as_str().unwrap_or("No response.").to_string())
}

pub async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "[::1]:50051".parse()?;
    let service = JarvisService::default();

    tracing::info!("JARVIS gRPC server listening on {}", addr);

    Server::builder()
        .add_service(JarvisBrainServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}
