use tracing_subscriber;

mod server;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::init();
    tracing::info!("JARVIS Core starting...");

    server::run().await?;

    Ok(())
}
