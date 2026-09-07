#!/bin/bash
# paperless-ngx post-consume script: send a Telegram message for every
# document once consumption (incl. OCR) has finished.
#
# Wired up in docker-compose.yml via PAPERLESS_POST_CONSUME_SCRIPT and given
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from /srv/docker/.env. Always exits 0
# so a notification problem never marks a consumed document as failed.

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "post-consume: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set; skipping notification" >&2
    exit 0
fi

html_escape() {
    sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

title="${DOCUMENT_FILE_NAME%.*}"
[ -z "$title" ] && title="${DOCUMENT_ORIGINAL_FILENAME:-document $DOCUMENT_ID}"
title=$(printf '%s' "$title" | html_escape)

text="📄 <b>Document processed</b>
${title}"

[ -n "$DOCUMENT_CORRESPONDENT" ] && text+="
From: $(printf '%s' "$DOCUMENT_CORRESPONDENT" | html_escape)"
[ -n "$DOCUMENT_TYPE" ] && text+="
Type: $(printf '%s' "$DOCUMENT_TYPE" | html_escape)"
[ -n "$DOCUMENT_TAGS" ] && text+="
Tags: $(printf '%s' "$DOCUMENT_TAGS" | html_escape)"
[ -n "$DOCUMENT_CREATED" ] && text+="
Date: ${DOCUMENT_CREATED%% *}"

if [ -n "$PAPERLESS_URL" ] && [ -n "$DOCUMENT_ID" ]; then
    text+="

<a href=\"${PAPERLESS_URL}/documents/${DOCUMENT_ID}/details\">Open in paperless</a>"
fi

# Telegram limit is 4096 characters
[ ${#text} -gt 4096 ] && text="${text:0:4093}..."

response=$(curl -s --max-time 15 -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode chat_id="$TELEGRAM_CHAT_ID" \
    --data-urlencode parse_mode="HTML" \
    --data-urlencode disable_web_page_preview="true" \
    --data-urlencode text="$text")

case "$response" in
    *'"ok":true'*) echo "post-consume: Telegram notification sent for document ${DOCUMENT_ID}" ;;
    *) echo "post-consume: Telegram notification failed: ${response:-no response}" >&2 ;;
esac
exit 0
