## What I Built
I used Claude to make a RAG tool that takes Larkfield's documents and uses the structure chunking method to answer questions a user has about Larkfield. It stores chunks in a Chroma database, retrieves the 4 most relevant chunks for a question, sends these chunks to Claude along with the question, and Claude returns an answer that includes citations. The first question I asked received an indefinite answer from Claude because there was conflicting data. Two files were listed as the current version of the document and contained conflicting information on the question I asked, so Claude didn't know which file to use. I looked at the files and saw that Version 8 (consumer-fee-schedule.html) was said to replace Version 7 (consumer-fee-schedule-v7.html), so I told Claude to move the Version 7 to an archive folder and omit it from the database. I then rebuilt the database and asked the question again, and Claude returned the correct answer using the correct current version of the document. For the other five runs, I asked Claude questions and it came up with mostly the correct answer for all of them, with a few caveats. For question 6, Claude's description of the documents was partly wrong; its description about what they cover was only a small portion of what they actually do cover. The reason for this limitation is that it could only use the 4 chunks it received, so it didn't know there was additional data that the documents covered that the 4 chunks didn't mention. Some of the other runs also had minor errors or missing information; some of the questions used wording that was not close enough to the documents' wording, so the answers may not have been as efficient as they could have. Overall though, Claude answered the remaining runs correctly.

## Why This Approach
Chunking method: structure.
Makes sense on its own: Partly. Some chunks depend on a definition in another section or point to text that isn't included in the chunk. An example is a chunk that includes 'as described below', but it is cut off there and doesn't include the description below it.
Label and source: Both. The label is in the chunk itself, as every chunk starts with 'Document' and 'Section' lines. The source file is stored as metadata by Chroma. I know this by asking Claude.
Short enough: Yes. Claude told me Chroma uses a 256-token limit, and that every chunk is short enough for the embedding model to read in full. The longest chunk is 155 tokens, while a typical one is 64 tokens.

## Example Run
Top 4 retrieved chunks:
  #1  similarity 0.246  share-certificates.html
      Document: Share Certificates Section: Dividend rates  - 6-month share certificate: 3.05% annual percentage yield (APY). - 12-month share certificate: ...
  #2  similarity 0.241  consumer-fee-schedule.html
      Document: Consumer Fee Schedule Section: Stop payments  A stop payment on a check costs $30 per request. One request can cover a single check or a ser...
  #3  similarity 0.200  consumer-fee-schedule.html
      Document: Consumer Fee Schedule Section: Checking account monthly fees  Everyday Checking has no monthly service fee. Plus Checking has a monthly serv...
  #4  similarity 0.187  share-certificates.html
      Document: Share Certificates Section: At maturity  When a share certificate matures, it renews automatically for the same term at the rate in effect o...

ANSWER
The provided documents don't contain information about mortgage rates. They only cover share certificate dividend rates, checking account fees, and stop payment fees [share-certificates.html] [consumer-fee-schedule.html].


## Reflection
What was wrong: The file 'consumer-fee-schedule-v7.html' caused the problem in Question 1. The retrieved chunks for Question 1 showed that v7 and v8 were almost the same, except they contained different prices, so Claude was not able to come up with one correct answer.
How I fixed it: Moved. I moved the document to a different folder, labeled 'larkfield-docs-archive', so it was not included in the reference documents. Then I rebuilt the database.
Prevention: Archive process. Larkfield should archive their old, outdated documents by moving them to an archive folder so they are not included in the reference documents. Then they need to rebuild the database so that this problem with an old version conflicting with the current version doesn't happen again.
Question 6: Answered. Retrieval returned two chunks each from the Share Certificates section and the Consumer Fee Schedule section. These sections mentioned payments, costs, fees, and rates, so I can kind of see why it would have retrieved these chunks and scored them with the highest similarity for this question.