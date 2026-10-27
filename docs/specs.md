I need database which hold payment transactions
Tables are transcation, payments , chargeback
these table can have columns with json input

load 100 rows of sample data

Phase1
I need service which should expose APIs which should handle following usecases , these API will be exposed as MCP tools , hence should have the capability to add dynamic filters, usecases are,
    1. pull list of transactions for a given date
    2. find transactions which have chargebacks within this month
    3. find the transactions for a given customerID
    4. find transactions associated with paymentID
    5. pull chargebacks happened in this month for a given customer. 

All data should be displayed in the UI with pagination
Application should be extendable to include new filter

I need Transaction Service to handle the API calls
I need User to present their ask in natural language, which should be interpreted by LLM to decide which API to call, what filters to send. 
Keep the user memory /context throughout the session 

APIs should be only select (no insert/update/delete)
Agent should sit infront of API calls to decide on filter,
Expose these APIs via MCP server so that LLM can make tool calls
APIs should be able to handle the where claude dynamically without intrducing any sql injection or security issues. 
LLM should perform where clause validation before executing it.

Phase2
I need RAG to store the frequently used queries embedded and always look for the match if present, else generate one and store the embedding for future retrieval. 
Embedding should give the right order of execution and only filters are substituted based on the current query.
Use redis cache to store the frequently searched pattern to avoid vector search (phase2)
data should be displayed in a table and should have the capability to click on one item , which should call approapriate API to pull the specific transaction detail


Language :
Python
FastAPI

Rules
Keep the solution modular, 
use chromaDB to store the embeddings
use claude anthropic API to make llm calls
Use voyager API to generate the embeddings
postgres SQL for the database storage
Solution should follow SOLID principals
Each function/variable name should be self explainatory. 
clean and DRY code. 
Should be well structured and self explainatory

