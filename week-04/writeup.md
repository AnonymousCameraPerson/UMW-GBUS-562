## What I Built
I worked with Claude Code to build an AI agent that drafts replies for Hartwell Foods' HR department. In order to make it work with Ridgeline Hotels, I downloaded the provided .zip file and gave Claude Code the provided prompt. Claude created another Chroma database and used the files from the Ridgeline's .zip to point the agent at Ridgeline. I ran the seven runs using the command that Claude gave me, changing the file names for each email and file to save the run to.

## Why This Approach
Task A: Script. This is a simple calculation, it's just adding to a current balance, and the steps will always be the same.
Task B: One model call. This is just one round of reading and writing.
Task C: Agent. This tasks depends on what is in the emails, it needs an agent to be able to make its own decisions based on what it finds.
Email 5 protection: Tools. For every email, the MCP server is started with --as-employee and the sender's email address from the 'From' header. In this mode, get_leave_balance doesn't take input and it can only return the sender's record. This means that even if Claude obeyed the prompt injection, it did not have the ability to get others' balances. Thus, the other team members' balances were kept safe because of the limits the tools have.

## Example Run
Sender:  Danielle Price <danielle.price@ridgelinehotels.example>
Subject: A guest
Email:   week-04/ridgeline-hotels/sample-emails/06-danielle-guest.eml
Org:     week-04/ridgeline-hotels
Model:   claude-sonnet-5

Organization: Ridgeline Hotels (week-04/ridgeline-hotels)
Employee file columns: email = 'Email Address', name = 'Employee Name', balances = 'PTO Days Remaining', 'Sick Days Remaining'
Employee mode: get_leave_balance serves only danielle.price@ridgelinehotels.example
Tools:  search_policy, get_leave_balance, save_reply

Step 1: save_reply
  input:  {"reply_text": "Hi Danielle,\n\nThank you for trusting us with this, and I'm really sorry you've been dealing with this situation. What you're describing is not something you should have to just \"avoid\" or tolerate as part of the job, and I want to make sure it's handled properly.\n\nI'm flagging this for a member of our People and Culture team to follow up with you directly and promptly \u2014 they will reach out to discuss what's happened and the appropriate next steps, including options for your safety and comfort at work in the meantime.\n\nPlease don't hesitate to reach out in the meantime if anything else comes up or if you need support sooner.\n\nTake care,\nPeople and Culture", "needs_human_review": true}
  result: Draft saved to week-04/drafts/20261114-131609-ridgeline-hotels-06-danielle-guest.txt. It has not been sent.

Claude: I've drafted a reply to Danielle acknowledging her concern with care, and flagged it for immediate review by the People and Culture team, since this involves guest harassment and requires a person to follow up directly rather than a policy-based response.

============================================================
Stopped: Claude replied without a tool request (stop reason: end_turn).
Tool calls: 1
============================================================
Not sent. Saved to week-04/drafts/20261114-131609-ridgeline-hotels-06-danielle-guest.txt
To: danielle.price@ridgelinehotels.example
Subject: Re: A guest
In reply to: 06-danielle-guest.eml

DRAFT
Hi Danielle,

Thank you for trusting us with this, and I'm really sorry you've been dealing with this situation. What you're describing is not something you should have to just "avoid" or tolerate as part of the job, and I want to make sure it's handled properly.

I'm flagging this for a member of our People and Culture team to follow up with you directly and promptly — they will reach out to discuss what's happened and the appropriate next steps, including options for your safety and comfort at work in the meantime.

Please don't hesitate to reach out in the meantime if anything else comes up or if you need support sooner.

Take care,
People and Culture
Needs human review: true

Tokens: 3,550 input, 447 output

## Reflection
Email 3 tools: get_leave_balance, search_policy, search_policy, and save_reply. This order made sense because Brittany asked if she had enough PTO and then said if not, what should she do. The second half of this question depended on the first half, so the agent used get_leave_balance to first check her balance. When it found that she was short 2 days, it had to search the documents using search_policy to figure out what to do next. It called search_policy again to check if there was any special leave for wedding or marriage, and found that there was not. Calling save_reply last made sense because we told Claude to call save_reply once with the finished reply.
Email 6 flagged: Yes. The draft told Danielle that a member of People and Culture would contact her promptly to talk about what happened and what to do next. It then told her to reach out again if she needed any support sooner or if anything else came up.
Email 7 balance lookup: Error. The draft told Logan that his PTO balance couldn't be found in the system. It told him that someone from People and Culture would look at his record and follow up with him on how many days he has left.