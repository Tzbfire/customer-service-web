from desk.export_markdown import heading, literal, message_markdown


def test_literal_message_cannot_break_out_of_markdown_code_fence():
    content = "hello\n```\n# fake section\n<script>alert(1)</script>\n````"
    result = literal(content)
    assert result.startswith("`````text\n") and result.endswith("\n`````\n")
    assert content in result
    assert "\n" not in heading("Buyer\n# header")
    assert "<script>" not in heading("<script>")


def test_markdown_preserves_cards_orders_and_safe_media():
    value = {
        "role": "customer",
        "platform_id": "12345678901234567890",
        "content": "hello",
        "presentation": {
            "body": "hello",
            "quote": "quote",
            "product": {
                "id": "123",
                "name": "Item [A]",
                "price": "¥95.00",
                "image_url": "https://img.pddpic.com/a(1).jpg",
                "url": "https://mobile.yangkeduo.com/goods.html?goods_id=123",
            },
            "order": {"id": "ORDER1", "spec": "Large", "status": "待发货"},
            "media": [
                {"kind": "video", "url": "https://video.pddpic.com/a.mp4"},
                {"kind": "image", "url": "javascript:alert(1)"},
            ],
        },
    }
    result = message_markdown(value, "2026-10-28 17:00:00")
    assert "12345678901234567890" in result and "¥95.00" in result and "ORDER1" in result
    assert "a%281%29.jpg" in result and "[查看视频]" in result
    assert "javascript:" not in result and "引用：" in result


def test_recalled_message_markdown_contains_only_the_recall_notice():
    result = message_markdown(
        {
            "role": "customer",
            "content": "original private message",
            "presentation": {"kind": "withdrawn", "body": "消息已撤回"},
        },
        "2026-10-28",
    )
    assert "消息已撤回" in result and "original private message" not in result
