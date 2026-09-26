import asyncio
import json

import httpx

from nest.vision.captioner import Captioner


def test_captioner_keeps_only_known_landmarks_and_valid_relations():
    text = json.dumps([
        {"object": "Yellow Ball", "relation": "behind", "landmark": "green chair",
         "near": "sofa", "confidence": 0.8},
        {"object": "sock", "relation": "levitating over", "landmark": "sofa"},
        {"object": "cat", "relation": "on", "landmark": "the moon"},
    ])
    out = Captioner.parse(text, ["green chair", "sofa"])
    assert out[0] == {"object": "yellow ball", "relation": "behind", "landmark": "green chair",
                      "near": "sofa", "confidence": 0.8}
    assert out[1]["relation"] == "near" and len(out) == 2
    assert Captioner.parse("not json", ["sofa"]) == []
    assert Captioner.parse('{"objects": []}', ["sofa"]) == []


def test_captioner_talks_to_ollama_chat():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        answer = [{"object": "pinecone", "relation": "under", "landmark": "table"}]
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})

    async def main():
        captioner = Captioner(transport=httpx.MockTransport(handler))
        out = await captioner.describe(b"\x89PNG fake", ["table"])
        await captioner.close()
        return out

    out = asyncio.run(main())
    assert out[0]["object"] == "pinecone"
    assert seen["format"] == "json" and seen["messages"][0]["images"]
