import boto3
import json
from datetime import datetime, timedelta, timezone

def lambda_handler(event, context):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Content-Type": "application/json"
    }

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    params = event.get("queryStringParameters") or {}
    days   = int(params.get("days", 7))
    action = params.get("action", "summary")
    service= params.get("service", None)

    client = boto3.client("ce", region_name="us-east-1")
    today  = datetime.now(timezone.utc).date()
    end    = str(today)
    start  = str(today - timedelta(days=days))

    try:
        if action == "summary":
            data = get_summary(client, start, end)
        elif action == "trend":
            data = get_trend(client, start, end)
        elif action == "usage" and service:
            data = get_breakdown(client, start, end, service, "USAGE_TYPE")
        elif action == "region" and service:
            data = get_breakdown(client, start, end, service, "REGION")
        elif action == "instance" and service:
            data = get_breakdown(client, start, end, service, "INSTANCE_TYPE")
        else:
            data = {"error": "Invalid action"}

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps(data)
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"error": str(e)})
        }

def get_summary(client, start, end):
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}]
    )
    services = {}
    for result in response["ResultsByTime"]:
        for group in result["Groups"]:
            svc    = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            services[svc] = services.get(svc, 0.0) + amount

    return {
        "services": [
            {"name": k, "cost": round(v, 4)}
            for k, v in sorted(services.items(), key=lambda x: -x[1])
            if v > 0.001
        ][:20]
    }

def get_trend(client, start, end):
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"]
    )
    return {
        "trend": [
            {
                "date":  r["TimePeriod"]["Start"],
                "total": round(sum(
                    float(g["Metrics"]["UnblendedCost"]["Amount"])
                    for g in r.get("Groups", [])
                ) or float(r["Total"]["UnblendedCost"]["Amount"]), 2)
            }
            for r in response["ResultsByTime"]
        ]
    }

def get_breakdown(client, start, end, service, dimension):
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": [service]}},
        GroupBy=[{"Type": "DIMENSION", "Key": dimension}]
    )
    breakdown = {}
    for result in response["ResultsByTime"]:
        for group in result["Groups"]:
            key    = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            breakdown[key] = breakdown.get(key, 0.0) + amount

    return {
        "service":   service,
        "dimension": dimension,
        "breakdown": [
            {"name": k, "cost": round(v, 4)}
            for k, v in sorted(breakdown.items(), key=lambda x: -x[1])
            if v > 0.001
        ]
    }
