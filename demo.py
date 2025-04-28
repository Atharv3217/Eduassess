import google.generativeai as genai

genai.configure(api_key="AIzaSyDnfpE0dqSjC3CC7lx5LXcZ1DMmGumsO-s")

models = genai.list_models()
for model in models:
    print(model.name)
