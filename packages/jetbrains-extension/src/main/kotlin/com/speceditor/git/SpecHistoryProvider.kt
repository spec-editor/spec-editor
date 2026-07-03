package com.speceditor.git

import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import git4idea.GitUtil
import git4idea.history.GitHistoryUtils
import git4idea.repo.GitRepositoryManager

/**
 * Wraps git4idea API for spec-editor needs: history, diff, branches.
 *
 * JetBrains IDE has git4idea built-in, which is far richer than
 * shelling out to `git` CLI or VSCode's `scm` API.
 */
class SpecHistoryProvider(private val project: Project) {

    /**
     * Get commit history for a file.
     */
    fun getHistory(file: VirtualFile, maxCount: Int = 50): List<CommitInfo> {
        val repo = GitRepositoryManager.getInstance(project)
            .getRepositoryForFile(file)
            ?: return emptyList()

        return try {
            GitHistoryUtils.history(project, file)
                .take(maxCount)
                .map { commit ->
                    CommitInfo(
                        hash = commit.id.asString(),
                        author = commit.author.name,
                        date = commit.authorDate.toString(),
                        message = commit.fullMessage.lines().firstOrNull() ?: ""
                    )
                }
        } catch (e: Exception) {
            emptyList()
        }
    }

    /**
     * Get list of branches in the repository containing this file.
     */
    fun getBranches(file: VirtualFile): List<String> {
        val repo = GitRepositoryManager.getInstance(project)
            .getRepositoryForFile(file)
            ?: return emptyList()

        return repo.branches.localBranches
            .map { it.name }
            .sorted()
    }

    /**
     * Find the Git repository root for a project path.
     */
    fun findRepoRoot(basePath: String): VirtualFile? {
        val vFile = GitUtil.findGitDir(java.io.File(basePath))
            ?: return null
        return vFile.parent
    }

    data class CommitInfo(
        val hash: String,
        val author: String,
        val date: String,
        val message: String
    )
}
