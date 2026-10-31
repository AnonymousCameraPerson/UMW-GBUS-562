## What I Built
I prompted Claude to build a prompt lab webpage that allows me to provide my API key and then talk to Claude through the webpage. I can create a new conversation with the 'New Conversation' button, and export the current conversation into a .md file using an 'Export' button. Using this website, I wrote four prompts for the four tasks, using a different technique for each. The first: zero-shot, the second: one-shot, the third: few-shot, and the fourth: chain-of-thought. For each task, I gave Claude general information about the coffee company and then more specific information for each task using the techniques.

## Why This Approach
Task 1 - Technique: zero-shot
The zero-shot technique fits task 1 because the task was to come up with 6 perk ideas, with no very specific requirements about the perks. The requirement of 25 words was simple enough that an example did not have to be provided, and one or more examples probably would have restricted Claude more than help. For creative or general ideas, zero-shot is a good technique to go with.

Task 2 - Technique: one-shot
The one-shot technique fits task 2 because there is one specific format for bean cards. Not giving Claude an example would have allowed it to go with any format it wanted, which is not what we want. Giving one example is the best because there is only one right format.

Task 3 - Technique: few-shot
The few-shot technique fits task 3 because we are aiming to recreate the company's brand voice. Giving multiple examples of past replies is a good way to do this. Giving one example would likely restrict Claude, as it would try to stick close to that one example, treating it as the only right way to write a reply. Giving no examples would give it too much freedom and it would probably not match the company's brand voice very well.

Task 4 - Technique: chain-of-thought
The chain-of-thought technique fits task 4 because this task had to take many factors into consideration and compare choices. Chain-of-thought is a good technique for reasoning and analysis. Giving Claude a guide on how to think through the decision-making process also helps to clarify your priorities and what matters most about the decision you make.

## Example Run
Task 2:
### You

Wildframe Coffee Roasters is a fictional specialty coffee roaster that sells beans online and through a monthly subscription. Brand voice: warm, plainspoken, and a little irreverent, with no corporate jargon. Customers: environmentally conscious urban professionals.
Wildframe is adding three coffees to its shop, and every coffee gets a bean card in the house format shown in the Brand Kit. Make one card for each coffee below. Required format: three cards in the house format, with the same labels in the same order.
- Yirgacheffe, Ethiopia. Light roast. Notes: jasmine, lemon, bergamot. Best as pour-over.
- Cerrado, Brazil. Medium-dark roast. Notes: peanut, milk chocolate, low acidity. Good for espresso or French press.
- Chiapas, Mexico. Medium roast. Notes: almond, caramel, orange peel. Good for drip or AeroPress.

Here is the Brand Kit house card format:
House bean card (the format every bean card follows):

HUILA, COLOMBIA
Roast: Medium
Tastes like: red apple, brown sugar, cocoa
Brew it: Pour-over or drip
Real talk: The coffee that converted our office's tea drinker. Mostly.

### Claude

Here are the three bean cards in the house format:

**YIRGACHEFFE, ETHIOPIA**
Roast: Light
Tastes like: jasmine, lemon, bergamot
Brew it: Pour-over
Real talk: Smells like a flower shop, drinks like sunshine. Your French press will just bully this one — give it the pour-over treatment it deserves.

**CERRADO, BRAZIL**
Roast: Medium-dark
Tastes like: peanut, milk chocolate, low acidity
Brew it: Espresso or French press
Real talk: The coffee equivalent of a weighted blanket. Low acid, high comfort, zero attitude.

**CHIAPAS, MEXICO**
Roast: Medium
Tastes like: almond, caramel, orange peel
Brew it: Drip or AeroPress
Real talk: Easygoing and a little bit fancy, like a Tuesday that feels like a Friday. Works in any mug, impresses in any cup.

### You

You did not give the correct format. You included two asterisks on either side of the location heading. This was not in the house format. Please redo it, removing the asterisks.

### Claude

Here are the three bean cards in the house format:

YIRGACHEFFE, ETHIOPIA
Roast: Light
Tastes like: jasmine, lemon, bergamot
Brew it: Pour-over
Real talk: Smells like a flower shop, drinks like sunshine. Your French press will just bully this one — give it the pour-over treatment it deserves.

CERRADO, BRAZIL
Roast: Medium-dark
Tastes like: peanut, milk chocolate, low acidity
Brew it: Espresso or French press
Real talk: The coffee equivalent of a weighted blanket. Low acid, high comfort, zero attitude.

CHIAPAS, MEXICO
Roast: Medium
Tastes like: almond, caramel, orange peel
Brew it: Drip or AeroPress
Real talk: Easygoing and a little bit fancy, like a Tuesday that feels like a Friday. Works in any mug, impresses in any cup.

## Reflection
Overall, I think the technique choices I made fit the tasks pretty well. The first was a general, creative task so zero-shot seemed a good choice. The second depended on one defined format, so one-shot seemed like a good choice. The third involved recreating a certain style and tone, so the few-shot seemed like a good choice. The fourth was a task involving analysis and reasoning, so chain-of-thought seemed like a good choice.
I think the chain-of-thought technique made the biggest difference out of all the techniques. It was important in task 4 to use a technique that guided Claude in its decision-making process and what to pay attention to and take into account. It was interesting because Claude included all my points in the reasoning it gave and how it came to the conclusion it did.
The choice I am least sure of is one-shot for task 2. The reason I am not too confident in this choice is because Claude inserted asterisks randomly into the card when they were not in the original format. Using the few-shot technique may have been more helpful, including multiple examples of what the card format looks like so Claude may not have made this mistake. 