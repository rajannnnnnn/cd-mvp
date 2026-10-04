# Demo server on AWS (Terraform)

One `t3.medium` (2 vCPU, 4 GB, Ubuntu 24.04) in the default VPC of your AWS account. On first boot it installs Docker, builds the project and
starts it with demo data. Cost: about $0.04 an hour. **Destroy it when you are done.**

```bash
cd deploy/terraform/aws
terraform init
terraform apply -var 'allowed_ips=["YOUR.PUBLIC.IP/32"]'      # add your co-founders' addresses to the list
# wait 10-15 minutes, then open the "url" printed at the end
terraform destroy -var 'allowed_ips=["YOUR.PUBLIC.IP/32"]'    # removes everything and stops the billing
```

Needs AWS credentials in your shell (`aws configure`, or the `AWS_*` environment variables) and a repository the server can download over https.
Add `-var 'ssh_key_name=my-key'` if you want SSH access, and `-var 'domain=demo.example.com'` for HTTPS on your own domain.
The demo accepts any login code, so keep `allowed_ips` to people you trust.
