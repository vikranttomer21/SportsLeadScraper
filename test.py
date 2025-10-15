from gpt4free import forefront

# create an instance
ai = forefront.Completion.create

prompt = "Analyze this webpage and extract any contact emails and phone numbers: hhttps://www.footballdelhi.com/"

response = ai(prompt=prompt, provider="forefront")

print(response)
