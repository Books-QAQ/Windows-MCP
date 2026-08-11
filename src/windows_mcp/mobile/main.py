import click

from windows_mcp.env import load_project_dotenv


@click.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Host to bind the mobile gateway.")
@click.option("--port", default=8787, type=int, show_default=True, help="Port for the mobile gateway.")
def main(host: str, port: int) -> None:
    """Run the mobile gateway service."""
    import uvicorn

    load_project_dotenv()
    uvicorn.run("windows_mcp.mobile.api:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    main()
