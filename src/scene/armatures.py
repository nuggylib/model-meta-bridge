import bpy


def armature_reports():
    return [
        {
            "name": o.name,
            "bones": [
                {"name": b.name, "parent": b.parent.name if b.parent else None}
                for b in o.data.bones
            ],
        }
        for o in bpy.context.scene.objects
        if o.type == "ARMATURE"
    ]
