# First Run Guide - Hopita AI Office Assistant

Welcome to Hopita AI Office Assistant! Follow these steps to initialize your agent for the first time.

## 1. Configure Telegram
Ensure `TELEGRAM_BOT_TOKEN` is set in your `.env` file. Talk to BotFather to create a bot if you haven't.

## 2. Configure Workspace
The agent requires a local workspace directory to store files, databases, and logs. It will default to `./workspace` if not configured. Ensure permissions are granted.

## 3. Configure Timezone
By default, the system timezone is `Asia/Ho_Chi_Minh`. You can change this by using the `/settings` command inside Telegram once the bot is running.

## 4. Connect Odoo (Optional)
Set `ODOO_URL`, `ODOO_DB`, `ODOO_ADMIN_USERNAME`, and `ODOO_API_KEY` in `.env`.
When chatting with the bot, use `/login <email> <password>` to verify your employee account.

## 5. Start the Agent
Run the polling script:
```bash
python -m scripts.run_telegram_bot_polling
```

## 6. Run Health Check
Send `/status` to the bot to ensure all databases and providers are connected.
Send `/accounts` to verify connected ERP and OAuth identities.

You are now ready to use Hopita!
