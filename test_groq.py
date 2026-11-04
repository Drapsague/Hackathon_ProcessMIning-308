import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# liste les modèles dispo, ça valide la clé au passage
for m in client.models.list().data:
    print(m.id)

resp = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[{"role": "user", "content": "Réponds juste 'ok'."}],
)
print(resp.choices[0].message.content)