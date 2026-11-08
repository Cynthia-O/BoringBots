# RedundancyBot
This bot will have three jobs:
- Execute Python scripts to cluster potentially related or duplicate files by their name and file extension.
- Analyze those clusters to propose which files are older versions or convenience copies of other files.
- Explain what it has found to the end user to help that user select files to be deleted. The agent itself will not have the ability to delete the files, this will be the human's job.

Initial scope:
- Local Mac-hosted files and Google Drive for now V1.
- All file extensions. Cluster within filetype, for any filetype that has more than one representative in the corpus.