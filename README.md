# AWS Cost Report
Automated daily AWS cost comparison report delivered by email.

## What it does
* Compares yesterday's spend vs the day before across all AWS services
* Highlights cost spikes (20%+ increases above $1)
* Shows a 7-day spend trend
* Shows a month-to-date spend breakdown
* Shows monthly totals for the last 3 months + current month (MTD), each with a daily average
* Excludes lump-sum charges (Drata, Tax, Support) from monthly totals and daily averages so they reflect true day-to-day infrastructure spend
* Emails a formatted HTML report via AWS SES every morning

## Tech stack
* Python 3.12
* AWS Lambda (serverless, runs automatically)
* AWS Cost Explorer API
* AWS SES (email delivery)
* AWS EventBridge (daily schedule)

## Setup
1. Enable AWS Cost Explorer in the Billing console
2. Verify your sender email in AWS SES
3. Deploy `cost_report.py` as a Lambda function
4. Set the handler to `cost_report.lambda_handler`
5. Attach IAM permissions for Cost Explorer, SES, and CloudWatch Logs
6. Add an EventBridge trigger with cron `0 8 * * ? *`

## Configuration
Set the following environment variables in your Lambda function:

Lambda Console → Configuration → Environment variables → Edit

| Variable | Description |
|---|---|
| `SES_SENDER_EMAIL` | SES verified sender email |
| `SES_RECIPIENTS` | Comma-separated list of recipient emails e.g. `a@example.com,b@example.com` |
| `SPIKE_THRESHOLD` | % increase to trigger a spike alert (default: 20) |

## Excluded services
The following services are excluded from monthly totals and daily averages as they are billed as lump sums and would skew the numbers. To add or remove services, update the `EXCLUDED_SERVICES` set near the top of `cost_report.py`.

* Drata Security & Compliance Automation Platform
* Tax
* Support (Enterprise)

## IAM permissions required
```json
{
  "Effect": "Allow",
  "Action": [
    "ce:GetCostAndUsage",
    "ses:SendEmail",
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents"
  ],
  "Resource": "*"
}
```
