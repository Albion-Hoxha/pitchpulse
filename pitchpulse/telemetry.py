"""OpenTelemetry to Azure Application Insights: requests, agent runs, model calls and Fact-Checker outcomes.

Off unless APPLICATIONINSIGHTS_CONNECTION_STRING is set. In Azure the exporter signs in with the app's
managed identity (Entra ID), so the Application Insights resource can keep local auth disabled.
"""
import os

from opentelemetry import trace

tracer = trace.get_tracer("pitchpulse")  # a proxy: picks up the exporter once setup() has run


def setup(app):
    connection = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection:
        return False
    from azure.identity import DefaultAzureCredential
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    os.environ.setdefault("OTEL_SERVICE_NAME", "pitchpulse")  # the app's name on the Application map
    configure_azure_monitor(connection_string=connection, credential=DefaultAzureCredential(),
                            logger_name="pitchpulse")
    # app was built from the class imported before configuration; skip one span per streamed SSE chunk
    FastAPIInstrumentor.instrument_app(app, exclude_spans=["send", "receive"])
    return True  # Microsoft Agent Framework spans (agent runs, MCP calls) flow to the same exporter
