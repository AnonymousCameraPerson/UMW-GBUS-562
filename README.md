# UMW-GBUS-562
Repository for GBUS 562 (AI Language Models)

## About
This repository holds materials created for GBUS 562: AI Language Models at the University of Mary Washington (UMW). Materials are organized into folders by week.

## Weekly Work
1. week-01
   - [Connect Four](week-01/connect-four.html): a single-page browser game of Connect Four. Play against a computer opponent with three difficulty levels, or switch to two-player mode to take turns with a friend on the same screen.
2. week-02
   - [Prompt Lab](week-02/prompt-lab.html): a web app for testing prompts against Claude, built with Claude Code.
3. week-03
   - Week 3: a [RAG tool](week-03/ask.py) that answers questions from Copperline Manufacturing's finance documents, built with Claude Code.
4. week-04
   - Week 4: an [AI agent](week-04/agent.py) that prepares replies to HR emails using tools from an [MCP server](week-04/hr_mcp_server.py), built with Claude Code. It works for any organization folder (`--org`): [Hartwell Foods](week-04/hartwell-hr/) and [Ridgeline Hotels](week-04/ridgeline-hotels/), with seven [Ridgeline runs](week-04/runs/) and a [write-up](week-04/writeup.md).
5. week-05
   - Week 5: a team of AI agents that prepares a company's response to supplier emails, built with Claude Code. A supply analyst, two writers, and a reviewer are coordinated either by a fixed-order [pipeline](week-05/pipeline.py) or by an [orchestrator](week-05/orchestrator.py) agent that chooses which specialists to call. It works for any company folder (`--org`): [Brookfield Bikes](week-05/brookfield-bikes/) and [Copperline Coffee](week-05/copperline-coffee/), with eight [Copperline runs](week-05/runs/) (each of its four supplier emails under both coordinators) the saved [drafts](week-05/drafts/), and a [write-up](week-05/writeup.md).
