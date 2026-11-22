package io.github.chaoszero6.openrar.core

/**
 * Folder view over an archive's flat entry list. Archives often omit folder entries ("a/b/c.txt" only),
 * so folders are derived from paths; folder sizes are the totals of everything inside.
 */
class ArchiveTree(entries: List<ArchiveEntry>) {
    data class Node(val path: String, val name: String, val isDirectory: Boolean, val size: Long,
                    val packedSize: Long?, val modified: Long?, val encrypted: Boolean, val files: Int)

    private val children = HashMap<String, MutableMap<String, Node>>()

    init {
        val files = entries.filter { !it.isDirectory }
        val dirSize = HashMap<String, Long>()
        val dirCount = HashMap<String, Int>()
        val dirTime = HashMap<String, Long>()
        for (e in files) {
            var p = e.parent
            while (true) {
                dirSize[p] = (dirSize[p] ?: 0) + e.size
                dirCount[p] = (dirCount[p] ?: 0) + 1
                if (p.isEmpty()) break
                p = p.substringBeforeLast('/', "")
            }
        }
        for (e in entries.filter { it.isDirectory }) e.modified?.let { dirTime[e.path] = it }
        fun ensureDir(path: String) {
            if (path.isEmpty()) return
            val parent = path.substringBeforeLast('/', "")
            val map = children.getOrPut(parent) { LinkedHashMap() }
            if (path !in map) {
                map[path] = Node(path, path.substringAfterLast('/'), true, dirSize[path] ?: 0, null, dirTime[path], false,
                    dirCount[path] ?: 0)
            }
            ensureDir(parent)
        }
        for (e in entries) {
            if (e.isDirectory) ensureDir(e.path) else {
                ensureDir(e.parent)
                children.getOrPut(e.parent) { LinkedHashMap() }[e.path] =
                    Node(e.path, e.name, false, e.size, e.packedSize, e.modified, e.encrypted, 1)
            }
        }
    }

    /** Folders first, then files, each sorted by name. */
    fun list(folder: String): List<Node> =
        children[Paths.normalize(folder)]?.values.orEmpty()
            .sortedWith(compareBy<Node>({ !it.isDirectory }, { it.name.lowercase() }))

    fun isFolder(path: String) = Paths.normalize(path).let { it.isEmpty() || children.containsKey(it) || children.values.any { m -> m[it]?.isDirectory == true } }
}
