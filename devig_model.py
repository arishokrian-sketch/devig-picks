name: DEVIG Daily Picks
 
on:
  schedule:
    # 11:00 UTC = 7:00am ET (6:00am during EDT — adjust if you want exact DST handling)
    - cron: '0 11 * * *'
  workflow_dispatch: {}   # lets you also trigger it manually from the Actions tab to test
 
jobs:
  run-picks:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4
 
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
 
      - name: Run daily model
        env:
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          TO_EMAIL: ${{ secrets.TO_EMAIL }}
          MIN_EV: '0.03'
          ENABLE_PROPS: 'false'   # flip to 'true' only after upgrading to an Odds API plan with player props
          PROPS_MAX_EVENTS: '6'
        run: python daily_run.py
