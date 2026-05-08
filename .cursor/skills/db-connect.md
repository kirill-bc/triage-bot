---
name: cloud-db-connect
description: Connect to BriteCore cloud development database using bc-local and Rancher. Use when connecting to dev database, accessing cloud database, running database queries on dev sites, or when user mentions bc-local, Rancher, or site database access.
allowed-tools: Bash, Read, AskUserQuestion
---

# BriteCore Cloud Database Connection

This skill establishes a connection to a BriteCore development site's database in the cloud using bc-local and Rancher.

## Process

1. **Get site name**: Ask user for the site name if not provided (e.g., bc19630newcode)

2. **Check RANCHER_TOKEN**: Run `echo $RANCHER_TOKEN` to check if set
   - If empty, ask user for their Rancher token
   - Token format: `token-fnt5b:zszs44ksm8h44t2t7s5qzhkjhgz4vtjfq9w9gcxv74hmhsv668ctfg`

3. **Run Python script**: Execute the connection script:
   ```bash
   python3 /Users/lucas.carvalho/projects/BriteCore/.claude/skills/cloud-db-connect/connect_db.py <site-name> <rancher-token>
   ```

   The script will:
   - Start port forwarding in background
   - Extract the port number
   - Retrieve database password
   - Identify the database name
   - Verify the connection
   - Return JSON with all connection details

4. **Display results**: Parse the JSON output and present to user:
   - Host: localhost
   - Port: (extracted port)
   - Username: (site name)
   - Password: (extracted password)
   - Database: (identified database)
   - Protocol: TCP
   - Port Forward PID: (for reference)

5. **Provide connection command**: Give user the final MySQL command:
   ```bash
   mysql -h localhost -P <port> -u <site-name> -p<password> <database> --protocol=TCP
   ```

## Important Notes

- The port forward process must stay running in the background (do not kill the PID)
- The database name may NOT match the site name (script identifies it automatically)
- If connection fails, the JSON output will include an "error" field

## Example Usage

```bash
# User provides: site-name = bc19630newcode, token = token-fnt5b:abc123...
python3 connect_db.py bc19630newcode token-fnt5b:abc123...

# Output:
{
  "success": true,
  "site_name": "bc19630newcode",
  "host": "localhost",
  "port": "58492",
  "username": "bc19630newcode",
  "password": "PetZzlYrntw9bsZQ",
  "database": "augusta",
  "protocol": "TCP",
  "port_forward_pid": 32125
}

# Final command:
mysql -h localhost -P 58492 -u bc19630newcode -pPetZzlYrntw9bsZQ augusta --protocol=TCP
```
