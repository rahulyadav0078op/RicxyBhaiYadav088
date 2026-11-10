# Procfile — Heroku process types
#
# A worker dyno, not a web dyno: the bot polls Telegram and never needs a port.
# A web dyno would idle-sleep and hit Heroku's 30s request timeout on big uploads.
worker: sh -c "python railway_setup.py && python bot.py"
